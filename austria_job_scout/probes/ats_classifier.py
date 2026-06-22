"""ATS classifier — pure pattern matching, no network.

Given a URL (and optionally a snippet of HTML), returns one of the
canonical ATS fingerprints from :data:`ATS_FINGERPRINTS`.

This is a *probe* — pure function, fully unit-testable with fixtures.
The orchestrator may call it before deciding to fetch (Tier 1 vs Tier 2
discrimination).

Hostname rules (priority order — first match wins):
    - boards-api.greenhouse.io / boards.greenhouse.io          → greenhouse
    - api.lever.co / jobs.lever.co                              → lever
    - api.smartrecruiters.com / jobs.smartrecruiters.com        → smartrecruiters
    - *.myworkdayjobs.com                                        → workday
    - *.jobs.personio.de / *.jobs.personio.com                  → personio
    - *.successfactors.com / *.successfactors.eu                 → successfactors
    - *.workable.com                                             → workable
    - *.recruitee.com                                            → recruitee
    - www.karriere.at / karriere.at                             → karriere_at
    - www.stepstone.at                                           → stepstone_at
    - www.jobs.at                                                → jobs_at
    - at.indeed.com / www.indeed.at                             → indeed_at
    - www.willhaben.at                                           → willhaben

HTML rules (only checked when URL doesn't already match a more specific
host, and only as a tie-breaker):
    - <meta name="ats"> or obvious `greenhouse` / `lever` JS bundle → ATS
    - "myworkdayjobs" in src/scripts                            → workday
    - generic HTML, no signals                                  → generic_html

Anything that doesn't match returns ``unknown``.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse


# Canonical ATS fingerprints — keep in sync with `config.SOURCE_PRIORITY`
ATS_FINGERPRINTS: tuple[str, ...] = (
    "greenhouse",
    "lever",
    "smartrecruiters",
    "workday",
    "personio",
    "successfactors",
    "workable",
    "recruitee",
    "karriere_at",
    "stepstone_at",
    "jobs_at",
    "indeed_at",
    "willhaben",
    "generic_html",
    "unknown",
)


# Hostname → ATS rules. Order matters (first match wins).
_HOST_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^boards-api\.greenhouse\.io$", re.I),     "greenhouse"),
    (re.compile(r"^boards\.greenhouse\.io$", re.I),         "greenhouse"),
    (re.compile(r"^api\.lever\.co$", re.I),                  "lever"),
    (re.compile(r"^api\.eu\.lever\.co$", re.I),              "lever"),
    (re.compile(r"^jobs\.lever\.co$", re.I),                 "lever"),
    (re.compile(r"^api\.smartrecruiters\.com$", re.I),       "smartrecruiters"),
    (re.compile(r"^jobs\.smartrecruiters\.com$", re.I),     "smartrecruiters"),
    (re.compile(r"\.myworkdayjobs\.com$", re.I),            "workday"),
    (re.compile(r"\.jobs\.personio\.de$", re.I),             "personio"),
    (re.compile(r"\.jobs\.personio\.com$", re.I),            "personio"),
    (re.compile(r"\.successfactors\.com$", re.I),           "successfactors"),
    (re.compile(r"\.successfactors\.eu$", re.I),            "successfactors"),
    (re.compile(r"\.workable\.com$", re.I),                  "workable"),
    (re.compile(r"\.recruitee\.com$", re.I),                 "recruitee"),
    (re.compile(r"^(www\.)?karriere\.at$", re.I),            "karriere_at"),
    (re.compile(r"^(www\.)?stepstone\.at$", re.I),          "stepstone_at"),
    (re.compile(r"^(www\.)?jobs\.at$", re.I),                "jobs_at"),
    (re.compile(r"^(at|www)\.indeed\.com$", re.I),           "indeed_at"),
    (re.compile(r"^(www\.)?indeed\.at$", re.I),              "indeed_at"),
    (re.compile(r"^(www\.)?willhaben\.at$", re.I),           "willhaben"),
)


# HTML sniffers — only used when URL hostname didn't match a more specific rule.
_HTML_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"myworkdayjobs\.com", re.I),                "workday"),
    (re.compile(r"greenhouse\.io", re.I),                    "greenhouse"),
    (re.compile(r"lever\.co", re.I),                         "lever"),
    (re.compile(r"smartrecruiters", re.I),                   "smartrecruiters"),
    (re.compile(r"personio", re.I),                          "personio"),
    (re.compile(r"workable", re.I),                         "workable"),
    (re.compile(r"recruitee", re.I),                         "recruitee"),
)


def _host_of(url: str) -> str:
    """Extract normalised hostname (lowercased, no port, no leading www.)."""
    h = (urlparse(url).hostname or "").lower()
    if h.startswith("www."):
        h = h[4:]
    return h


def classify(url: str, html_snippet: Optional[str] = None) -> str:
    """Return the ATS fingerprint for a URL (with optional HTML confirmation).

    Pure function. Safe to call before any network access.
    """
    if not url:
        return "unknown"
    host = _host_of(url)

    # 1. Hostname match — most specific signal
    for pattern, ats in _HOST_RULES:
        if pattern.search(host):
            return ats

    # 2. HTML sniff — only when hostname is generic
    if html_snippet:
        for pattern, ats in _HTML_RULES:
            if pattern.search(html_snippet[:8000]):  # cap snippet size
                return ats

    # 3. URL path heuristics — last resort
    low = url.lower()
    if "/jobs" in low or "/careers" in low or "/karriere" in low or "/stellenangebote" in low:
        return "generic_html"

    return "unknown"


def api_endpoint_for(ats: str, board_token: str) -> Optional[str]:
    """Return the canonical JSON/XML API endpoint for an ATS + board token.

    Returns None when the ATS doesn't have a public API endpoint we can hit
    (e.g. Workday, SuccessFactors) — those need HTML scraping instead.
    """
    if not board_token:
        return None
    if ats == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
    if ats == "lever":
        return f"https://api.lever.co/v0/postings/{board_token}?mode=json"
    if ats == "smartrecruiters":
        return f"https://api.smartrecruiters.com/v1/companies/{board_token}/postings"
    if ats == "workable":
        return f"https://www.workable.com/api/accounts/{board_token}?details=true"
    if ats == "recruitee":
        return f"https://{board_token}.recruitee.com/api/offers/"
    if ats == "personio":
        # Personio is per-tenant — token is the tenant slug; language defaults
        # to German on .de TLD. Caller may append ?language=en.
        return f"https://{board_token}.jobs.personio.de/xml"
    # Workday, SuccessFactors, karriere.at, jobs.at, stepstone.at, indeed_at,
    # willhaben — no public JSON/XML API we can hit from residential IP without
    # risk. Will be returned as HTML scrape targets by the orchestrator.
    return None


def is_html_scrape_only(ats: str) -> bool:
    """True if this ATS has no public JSON/XML and must be HTML-scraped."""
    return ats in {
        "workday",
        "successfactors",
        "karriere_at",
        "stepstone_at",
        "jobs_at",
        "indeed_at",
        "willhaben",
        "generic_html",
        "unknown",
    }
