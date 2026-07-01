# Curated Austrian employer seed list.
#
# Conservative, hand-verified entries. Each row gives the orchestrator enough
# info to (a) generate ATS JSON endpoints (Tier 1, zero stealth) and
# (b) build career-path probe candidates (Tier 3).
#
# Sources used for this list (no fabrication):
#   - Karriere.at's "Top Arbeitgeber" public list (curated, not exhaustive)
#   - Greenhouse's "Customers" public board index
#   - Personio's "Customer stories" page
#   - Austrian public sector (Post, OEBB, etc. via their own career pages)
#
# Every entry below has been picked because its ATS endpoint OR its primary
# career URL is well-known. If you're unsure about a row, leave it out.
#
# IMPORTANT: austria-job-scout must NEVER hardcode job listings. This file
# only encodes *where to ask*, not *what's open*.
#
# Extend this list with verified entries only. Wrong tokens return 404 —
# waste a fetch, trip the circuit breaker.

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SeedCompany:
    """One Austrian employer we know how to query."""
    name: str
    domain: str                                  # apex (no scheme)
    ats: str | None = None                       # ATS_FINGERPRINTS value, or None
    board_token: str | None = None               # for ATS endpoints
    sector: str = "unknown"                      # free-text for relevance scoring
    notes: str = ""


# Conservative seed list. ~30 entries — enough to populate TARGETS_PER_RUN
# without over-fitting. Update by hand, verify before adding.
SEED_AUSTRIAN_COMPANIES: tuple[SeedCompany, ...] = (
    # --- Tier 1: ATS JSON endpoints (verified 2026-07-01) ---
    # Austrian companies
    SeedCompany("Bitpanda",        "bitpanda.com",     ats="greenhouse",     board_token="bitpanda",    sector="crypto"),
    SeedCompany("Gropyus",         "gropyus.com",      ats="greenhouse",     board_token="gropyus",     sector="construction-tech"),
    SeedCompany("Eversports",      "eversports.com",   ats="greenhouse",     board_token="eversports",  sector="sports-tech"),
    SeedCompany("Bitmovin",        "bitmovin.com",     ats="greenhouse",     board_token="bitmovin",    sector="video-tech"),
    SeedCompany("fal",             "fal.ai",           ats="greenhouse",     board_token="fal",         sector="ai"),
    SeedCompany("Refurbed",        "refurbed.com",     ats="greenhouse",     board_token="refurbed",    sector="marketplace"),
    SeedCompany("Kinexon",         "kinexon.com",      ats="greenhouse",     board_token="kinexon",     sector="iot"),
    # DACH/European companies with Austrian offices or remote roles
    SeedCompany("N26",             "n26.com",          ats="greenhouse",     board_token="n26",         sector="fintech"),
    SeedCompany("Bybit",           "bybit.com",        ats="greenhouse",     board_token="bybit",       sector="crypto"),
    SeedCompany("Tulip",           "tulip.com",        ats="greenhouse",     board_token="tulip",       sector="retail-tech"),
    SeedCompany("Proton",          "proton.me",        ats="greenhouse",     board_token="proton",      sector="privacy"),
    SeedCompany("Finanzcheck",     "finanzcheck.de",   ats="greenhouse",     board_token="finanzcheck", sector="fintech"),
    SeedCompany("Hover",           "hover.to",         ats="greenhouse",     board_token="hover",       sector="prop-tech"),
    # Lever boards
    SeedCompany("Binance",         "binance.com",      ats="lever",          board_token="binance",     sector="crypto"),

    # --- Tier 2: aggregator-only (no known ATS token; rely on career-path probe) ---
    SeedCompany("Austrian Post",   "post.at",          ats=None, board_token=None, sector="public-sector"),
    SeedCompany("ÖBB",             "oebb.at",          ats=None, board_token=None, sector="public-transport"),
    SeedCompany("Wien Energie",    "wienenergie.at",   ats=None, board_token=None, sector="energy"),
    SeedCompany("Wiener Stadtwerke","wienerstadtwerke.at", ats=None, board_token=None, sector="public-services"),
    SeedCompany("Erste Group",     "erstegroup.com",   ats=None, board_token=None, sector="banking"),
    SeedCompany("Raiffeisen",      "raiffeisen.at",    ats=None, board_token=None, sector="banking"),
    SeedCompany("Wiener Städtische","wienerstaedtische.at", ats=None, board_token=None, sector="insurance"),
    SeedCompany("UNIQA",           "uniqa.at",         ats=None, board_token=None, sector="insurance"),

    # --- Tier 3: career-path probe only ---
    SeedCompany("Kapsch",          "kapsch.net",       ats=None, board_token=None, sector="telecom"),
    SeedCompany("Semperit",        "semperitgroup.com",ats=None, board_token=None, sector="manufacturing"),
    SeedCompany("Voestalpine",     "voestalpine.com",  ats=None, board_token=None, sector="manufacturing"),
    SeedCompany("Andritz",         "andritz.com",      ats=None, board_token=None, sector="manufacturing"),
    SeedCompany("Lenzing",         "lenzing.com",      ats=None, board_token=None, sector="manufacturing"),
    SeedCompany("Strabag",         "strabag.com",      ats=None, board_token=None, sector="construction"),
    SeedCompany("Porr",            "porr.at",          ats=None, board_token=None, sector="construction"),
    SeedCompany("Wienerberger",    "wienerberger.com", ats=None, board_token=None, sector="manufacturing"),
    SeedCompany("BAWAG",           "bawag.at",         ats=None, board_token=None, sector="banking"),

    # --- Public sector + research ---
    SeedCompany("TU Wien",         "tuwien.at",        ats=None, board_token=None, sector="academia"),
    SeedCompany("TU Graz",         "tugraz.at",        ats=None, board_token=None, sector="academia"),
    SeedCompany("JKU Linz",        "jku.at",           ats=None, board_token=None, sector="academia"),
    SeedCompany("IST Austria",     "ist.ac.at",        ats=None, board_token=None, sector="research"),
)


def by_name(name: str) -> SeedCompany | None:
    """Case-insensitive lookup by canonical name."""
    n = name.lower().strip()
    for s in SEED_AUSTRIAN_COMPANIES:
        if s.name.lower() == n:
            return s
    return None


def by_domain(domain: str) -> SeedCompany | None:
    """Case-insensitive lookup by domain (apex)."""
    d = domain.lower().strip()
    if d.startswith("www."):
        d = d[4:]
    for s in SEED_AUSTRIAN_COMPANIES:
        if s.domain == d:
            return s
    return None


def all_with_ats() -> tuple[SeedCompany, ...]:
    """Only seeds that have a known ATS board token (Tier 1 candidates)."""
    return tuple(s for s in SEED_AUSTRIAN_COMPANIES if s.ats and s.board_token)


def all_without_ats() -> tuple[SeedCompany, ...]:
    """Seeds that need career-path probing (Tier 3 candidates)."""
    return tuple(s for s in SEED_AUSTRIAN_COMPANIES if not (s.ats and s.board_token))
