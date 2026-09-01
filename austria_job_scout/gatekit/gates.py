"""Deterministic pre-index gates for austria-job-scout targets.

These run *before* a target is fetched/indexed, on the cheap text we
already have (the target's ``title`` + ``description`` + ``url``). Two
gates mirror the post-score gates in cv-profile-assessment:

* **Eligibility gate** — citizenship / work-rights / clearance.
* **Language gate**     — a required posting language the candidate has
  not declared.

Each returns ``("PASS" | "FAIL" | "FLAG", note)``. ``FAIL`` excludes the
target; ``FLAG`` is surfaced but kept; ``PASS`` is silent.

Only the candidate's *declared* profile fields are inspected. When those
are absent, gates stay silent (``PASS``, ``""``) — we never block on
missing data. This keeps the Pi-local, no-LLM, reproducible property.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

Verdict = str  # "PASS" | "FAIL" | "FLAG"
GateResult = Tuple[Verdict, str]

# --- Eligibility gate patterns (lowercase-contains) ---
_ELIGIBILITY_REQUIRED = [
    r"citizen(ship)?\b",
    r"permanent resident",
    r"\bpr\b",
    r"full working rights",
    r"security clearance",
    r"must be authorized to work in",
    r"legally entitled to work in",
]
_ELIGIBILITY_ACCEPTED = [
    r"international applicants? (are )?welcome",
    r"visa holders? considered",
    r"we sponsor",
    r"sponsorship available",
    r"\brelocation (support|assistance|package)\b",
]

# --- Language gate ---
_LEVEL_RANK = {
    "native": 5, "c2": 5, "fluent": 4, "c1": 4, "business": 4,
    "b2": 3, "intermediate": 3, "conversational": 2, "b1": 2,
    "basic": 1, "a2": 1, "beginner": 1, "a1": 1,
}
_KNOWN_LANGS = [
    "german", "deutsch", "english", "englisch", "french", "französisch",
    "spanish", "spanisch", "italian", "italienisch", "polish", "polnisch",
    "russian", "russisch", "dutch", "niederländisch", "czech", "tschechisch",
]
_ALIAS = {
    "deutsch": "german", "englisch": "english", "französisch": "french",
    "spanisch": "spanish", "italienisch": "italian", "polnisch": "polish",
    "russisch": "russian", "niederländisch": "dutch", "tschechisch": "czech",
}


def _norm(text: str) -> str:
    return (text or "").lower()


def check_eligibility_gate(profile: Dict, target: Dict) -> GateResult:
    """Eligibility / work-rights gate on a scout Target dict."""
    text = _norm(" ".join([
        target.get("title", ""),
        target.get("description", ""),
        target.get("url", ""),
    ]))
    if not text:
        return "PASS", ""

    for pat in _ELIGIBILITY_ACCEPTED:
        if re.search(pat, text):
            return "PASS", "Posting explicitly welcomes international applicants / sponsorship."

    for pat in _ELIGIBILITY_REQUIRED:
        if re.search(pat, text):
            work_auth = profile.get("basics", {}).get("work_authorization", [])
            if isinstance(work_auth, list) and any(work_auth):
                return "PASS", "Candidate declares work authorization; eligibility satisfied."
            return (
                "FAIL",
                "Posting implies a citizenship / work-right / clearance requirement "
                "the profile does not evidence. Verify before applying.",
            )
    return "PASS", ""


def _parse_level(text: str) -> Optional[int]:
    text = _norm(text)
    best = None
    for token, rank in _LEVEL_RANK.items():
        if re.search(rf"\b{re.escape(token)}\b", text):
            best = max(best or 0, rank)
    return best


def check_language_gate(profile: Dict, target: Dict) -> GateResult:
    """Language requirement gate on a scout Target dict."""
    text = _norm(" ".join([
        target.get("title", ""),
        target.get("description", ""),
        target.get("url", ""),
    ]))

    declared: Dict[str, Optional[int]] = {}
    for lang in profile.get("basics", {}).get("languages", []) or []:
        if isinstance(lang, dict):
            name = _norm(lang.get("language", ""))
            lvl = _parse_level(lang.get("fluency", ""))
        else:
            continue
        if name:
            declared[name] = lvl

    if not declared:
        return "PASS", ""

    required_signals = []
    for kl in _KNOWN_LANGS:
        canon = _ALIAS.get(kl, kl)
        if re.search(rf"(required|must|fluent|native|communicate in|level of)\b[^\.]*\b{re.escape(kl)}\b", text) or \
           re.search(rf"\b{re.escape(kl)}\b[^\.]*\b(required|must|fluent|native|necessary)\b", text):
            required_signals.append(canon)

    if not required_signals:
        return "PASS", ""

    for req in required_signals:
        if req not in declared:
            return "FAIL", (
                f"Posting requires '{req}' which is not declared in the "
                f"candidate's languages table."
            )
        posting_bar = _parse_level(text)
        declared_lvl = declared[req]
        if posting_bar is not None and declared_lvl is not None and posting_bar > declared_lvl:
            return "FLAG", (
                f"Posting bar for '{req}' reads higher than the declared level "
                f"({declared_lvl} vs ~{posting_bar}). Verify before applying."
            )
    return "PASS", "All required languages are declared at or above the posting's bar."


def run_gates(profile: Dict, target: Dict) -> Dict[str, Dict[str, str]]:
    """Run both gates; return {gate_name: {verdict, note}}."""
    elig_v, elig_n = check_eligibility_gate(profile, target)
    lang_v, lang_n = check_language_gate(profile, target)
    return {
        "eligibility_gate": {"verdict": elig_v, "note": elig_n},
        "language_gate": {"verdict": lang_v, "note": lang_n},
    }


def gate_targets(targets: list[Dict], profile: Dict,
                 exclude_fail: bool = True) -> Tuple[list[Dict], list[Dict]]:
    """Filter a list of Target dicts through both gates.

    Args:
        targets: scout Target dicts (each may carry ``title``,
            ``description``, ``url`` and arbitrarily many other keys).
        profile: candidate profile dict; must contain ``basics.languages``
            and/or ``basics.work_authorization`` for gates to fire.
        exclude_fail: when True, targets whose *either* gate returned
            ``"FAIL"`` are moved to the ``excluded`` list. ``FLAG`` targets
            are kept but annotated with their gate results.

    Returns:
        (kept, excluded) — both lists of (possibly augmented) Target dicts.
        Kept/flagged targets gain a ``_gates`` key:
        ``{"eligibility_gate": {...}, "language_gate": {...}}``.
    """
    import copy
    kept: list[Dict] = []
    excluded: list[Dict] = []
    for t in targets:
        gates = run_gates(profile, t)
        t = copy.deepcopy(t)  # don't mutate caller's (possibly shared) dict
        t["_gates"] = gates
        failed = any(g["verdict"] == "FAIL" for g in gates.values())
        if failed and exclude_fail:
            excluded.append(t)
        else:
            kept.append(t)
    return kept, excluded
