"""Tests for the gatekit package (pre-index gates + future LLM permission policy).

Covers:
  * eligibility / language gates on scout Target dicts
  * gate_targets() exclude-on-FAIL, keep-on-FLAG behaviour
  * CLI --profile opt-in wiring on `discover`
  * PermissionPolicy deny-by-default + allow-list (dormant scaffold)
"""

import json

import pytest

from austria_job_scout.gatekit import (
    check_eligibility_gate,
    check_language_gate,
    gate_targets,
    run_gates,
)
from austria_job_scout.gatekit.permissions import (
    ALLOWED_PERMISSIONS,
    PermissionDecision,
    PermissionPolicy,
)


# --- fixtures -------------------------------------------------------------

def _profile(languages=None, work_auth=None):
    basics = {}
    if languages is not None:
        basics["languages"] = languages
    if work_auth is not None:
        basics["work_authorization"] = work_auth
    return {"basics": basics}


SENIOR_PROFILE = _profile(
    languages=[{"language": "German", "fluency": "C1"},
               {"language": "English", "fluency": "C1"}],
    work_auth=["EU citizen"],
)

# Profile with NO declared work authorization → eligibility gate cannot
# satisfy a citizenship-required posting (used for FAIL assertions).
NO_AUTH_PROFILE = _profile(
    languages=[{"language": "German", "fluency": "C1"},
               {"language": "English", "fluency": "C1"}],
)

US_ONLY_TARGET = {
    "url": "https://job.example/citizen",
    "title": "Backend Engineer",
    "description": "Must be a US citizen or permanent resident. No sponsorship.",
}
GERMAN_C2_TARGET = {
    "url": "https://job.example/de",
    "title": "Backend Engineer",
    "description": "Fluent German (C2) required; must communicate in German daily.",
}
WELCOME_TARGET = {
    "url": "https://job.example/intl",
    "title": "Engineer",
    "description": "We sponsor visas; international applicants are welcome.",
}


# --- gates ----------------------------------------------------------------

def test_eligibility_fail_on_us_citizen():
    v, note = check_eligibility_gate(NO_AUTH_PROFILE, US_ONLY_TARGET)
    assert v == "FAIL"
    assert "citizenship" in note.lower() or "work-right" in note.lower()


def test_eligibility_pass_when_sponsored():
    v, _ = check_eligibility_gate(NO_AUTH_PROFILE, WELCOME_TARGET)
    assert v == "PASS"


def test_eligibility_pass_when_no_text():
    v, note = check_eligibility_gate(SENIOR_PROFILE, {"url": "x", "title": "", "description": ""})
    assert v == "PASS" and note == ""


def test_language_fail_when_required_undeclared():
    # Profile declares German/English only; target demands Polish.
    target = {"title": "Dev", "description": "Polish required at B2 level.",
              "url": "https://x/pl"}
    v, note = check_language_gate(SENIOR_PROFILE, target)
    assert v == "FAIL"
    assert "polish" in note.lower()


def test_language_flag_when_bar_higher_than_declared():
    # Profile declares German C1, target wants C2.
    v, _ = check_language_gate(SENIOR_PROFILE, GERMAN_C2_TARGET)
    assert v == "FLAG"


def test_language_pass_when_declared_ok():
    target = {"title": "Dev", "description": "English required (B2).",
              "url": "https://x/en"}
    v, _ = check_language_gate(SENIOR_PROFILE, target)
    assert v == "PASS"


def test_language_silent_without_profile_langs():
    profile = _profile()  # no languages
    target = {"title": "Dev", "description": "German required C1.", "url": "x"}
    v, note = check_language_gate(profile, target)
    assert v == "PASS" and note == ""


def test_run_gates_shape():
    g = run_gates(NO_AUTH_PROFILE, US_ONLY_TARGET)
    assert set(g) == {"eligibility_gate", "language_gate"}
    assert g["eligibility_gate"]["verdict"] == "FAIL"


# --- gate_targets ---------------------------------------------------------

def test_gate_targets_excludes_fail_keeps_flag():
    targets = [US_ONLY_TARGET, GERMAN_C2_TARGET, WELCOME_TARGET]
    kept, excluded = gate_targets(targets, NO_AUTH_PROFILE, exclude_fail=True)
    # US-only fails → excluded; German-C2 flags → kept (annotated); welcome → kept.
    assert len(excluded) == 1
    assert excluded[0]["url"] == US_ONLY_TARGET["url"]
    assert len(kept) == 2
    # Kept targets carry the _gates annotation.
    for t in kept:
        assert "_gates" in t
    flagged = [t for t in kept if t["_gates"]["language_gate"]["verdict"] == "FLAG"]
    assert flagged and flagged[0]["url"] == GERMAN_C2_TARGET["url"]


def test_gate_targets_no_profile_keeps_all():
    targets = [GERMAN_C2_TARGET, WELCOME_TARGET]
    kept, excluded = gate_targets(targets, _profile(), exclude_fail=True)
    assert len(kept) == 2 and len(excluded) == 0


# --- CLI wiring -----------------------------------------------------------

def test_discover_respects_profile_flag(tmp_path, capsys):
    from austria_job_scout.cli import build_parser, _load_profile_arg

    # No work authorization declared → eligibility gate will FAIL a
    # US-citizen-required target (if one is discovered). We just assert the
    # wiring runs and records gated_excluded without error.
    profile = {"basics": {"languages": [{"language": "German", "fluency": "C1"}]}}
    prof_path = tmp_path / "profile.json"
    prof_path.write_text(json.dumps(profile), encoding="utf-8")
    loaded = _load_profile_arg(str(prof_path))
    assert loaded == profile

    ref = {"source": "role_name", "raw_text": "Senior Backend Engineer",
           "title": "Senior Backend Engineer", "role_query": "backend",
           "skills": ["python"], "language": "en"}
    ref_path = tmp_path / "ref.json"
    ref_path.write_text(json.dumps(ref), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args([
        "discover", "--reference", str(ref_path),
        "--no-seeds", "--no-aggregators", "--profile", str(prof_path),
    ])
    rc = args.func(args)
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "gated_excluded" in payload
    assert "targets" in payload


def test_discover_without_profile_skips_gate(tmp_path, capsys):
    from austria_job_scout.cli import build_parser
    ref = {"source": "role_name", "raw_text": "Dev",
           "title": "Dev", "role_query": "dev", "skills": []}
    ref_path = tmp_path / "ref.json"
    ref_path.write_text(json.dumps(ref), encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args([
        "discover", "--reference", str(ref_path),
        "--no-seeds", "--no-aggregators",
    ])
    rc = args.func(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["gated_excluded"] == 0


# --- permission policy (dormant) ------------------------------------------

def test_policy_denies_when_disabled():
    p = PermissionPolicy(enabled=False)
    assert p.decide("Bash", "rm -rf /") == PermissionDecision.DENY
    assert p.decide("Read", "file.txt") == PermissionDecision.DENY


def test_policy_default_disabled_from_env(monkeypatch):
    monkeypatch.delenv("AJS_AGENT_MODE", raising=False)
    p = PermissionPolicy.from_env()
    assert p.enabled is False
    assert p.decide("Read", "x") == PermissionDecision.DENY


def test_policy_allows_listed_when_enabled():
    p = PermissionPolicy(enabled=True)
    assert p.decide("Read", "file.txt") == PermissionDecision.ALLOW
    # Our own read-only subcommand prefix is allowed.
    assert p.decide(
        "Bash", "python -m austria_job_scout discover --reference r.json"
    ) == PermissionDecision.ALLOW


def test_policy_denies_destructive_even_when_enabled():
    p = PermissionPolicy(enabled=True)
    assert p.decide("Bash", "git push origin master") == PermissionDecision.DENY
    assert p.decide("Bash", "rm -rf /tmp/x") == PermissionDecision.DENY
    assert p.decide("Write", "out.txt") == PermissionDecision.DENY


def test_policy_asks_on_unknown_tool():
    p = PermissionPolicy(enabled=True)
    # A tool not in the deny list and not in the allow list → ASK
    # (never auto-run). 'WebFetch' is itself denied, so use a neutral unknown.
    assert p.decide("ScrapeLinkedIn", "https://x") == PermissionDecision.ASK


def test_audit_replays_calls():
    p = PermissionPolicy(enabled=True)
    calls = [("Read", "a"), ("Bash", "git push"), ("Bash", "python -m austria_job_scout db-stats")]
    result = p.audit(calls)
    assert result[0][2] == PermissionDecision.ALLOW
    assert result[1][2] == PermissionDecision.DENY
    assert result[2][2] == PermissionDecision.ALLOW
