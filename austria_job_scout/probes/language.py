"""Heuristic language detection for the reference text.

This is a *probe*, not a full NPL pipeline. It returns one of:
  - ``"de"`` — clearly German (German keywords dominate)
  - ``"en"`` — clearly English
  - ``"mixed"`` — both detected, neither dominates by > 2x
  - ``"unknown"`` — text is empty, too short, or no signal

The threshold is intentionally low — we only need to decide whether to query
``/xml?language=de`` vs ``/xml?language=en`` on Personio, or whether to feed
the karriere.at search with German or English keywords. False "mixed" is fine.

Replace with `lingua-py` or `langdetect` later if accuracy matters.
"""
from __future__ import annotations

import re
from collections import Counter

# German stopword-like markers — high signal, low false positives.
_DE_MARKERS = frozenset(
    """
    und der die das ist nicht sie wir ich eine ein zu mit auf für von dem den
    auch als über nach wie noch nur sehr bei oder aber wenn dann weil hier
    dort unter zwischen durch gegen ohne um schon mehr jetzt können werden
    muss soll will darf haben wird sind war waren waren gewesen würde würden
    könnte könnten müsste müssten sollte sollten wollte wollten
    ihrer ihre seinem seinen seiner unser ihre ihre deinem deiner eurem eurer
    stellenangebot unternehmen mitarbeiter bewerbung kenntnisse erfahrung
    gehalt vollzeit teilzeit homeoffice
    """.split()
)

_EN_MARKERS = frozenset(
    """
    the and is are was were be been being have has had do does did will would
    should could may might must shall can of to in for on with at by from as
    it this that these those we you they he she his her its our their our your
    about into through during before after above below up down out off over
    under again further then once here there when where why how all any both
    each few more most other some such no nor not only own same so than too
    very job position role company team experience skills requirements
    responsibilities salary benefits remote hybrid
    """.split()
)

# Austrian / German "ß" and umlaut are also strong DE signals — but not as
# unique. We scan raw tokens for them.
_DE_SPECIAL_CHARS = re.compile(r"[äöüÄÖÜß]")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÄÖÜäöüß]{2,}", text.lower())


def detect(text: str) -> str:
    """Return one of 'de' / 'en' / 'mixed' / 'unknown'."""
    if not text:
        return "unknown"
    toks = _tokens(text)
    if len(toks) < 5:
        return "unknown"

    c = Counter(toks)
    de_hits = sum(c[t] for t in c if t in _DE_MARKERS)
    en_hits = sum(c[t] for t in c if t in _EN_MARKERS)

    # Special-char signal: ä/ö/ü/ß are German. Weight generously.
    special_hits = len(_DE_SPECIAL_CHARS.findall(text))

    de_score = de_hits + special_hits * 2
    en_score = en_hits

    if de_score == 0 and en_score == 0:
        return "unknown"
    if de_score == 0:
        return "en"
    if en_score == 0:
        return "de"

    ratio = de_score / max(en_score, 1)
    if ratio >= 2.0:
        return "de"
    if ratio <= 0.5:
        return "en"
    return "mixed"


def language_search_query(text: str, role: str | None = None) -> dict[str, str]:
    """Build a language-appropriate search query for aggregators.

    Returns ``{"de": <german query>, "en": <english query>}``. The caller can
    pick the one matching the detected language.
    """
    detected = detect(text)
    r = (role or "").strip()
    if detected == "de" or detected == "mixed":
        # Translate a handful of common English role names to German for karriere.at.
        translations = {
            "developer": "Entwickler",
            "engineer": "Ingenieur",
            "senior": "Senior",
            "junior": "Junior",
            "backend": "Backend",
            "frontend": "Frontend",
            "fullstack": "Fullstack",
            "data scientist": "Datenwissenschaftler",
            "machine learning": "Maschinelles Lernen",
        }
        de_query = r
        for en, de in translations.items():
            de_query = re.sub(rf"\b{en}\b", de, de_query, flags=re.IGNORECASE)
        return {"de": de_query, "en": r}
    return {"de": r, "en": r}
