"""Configuration: paths, env loading, sane defaults.

All knobs that the user can override live here. CLI subcommands read from
this module, not from os.environ directly.
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PACKAGE_ROOT: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = PACKAGE_ROOT.parent

# Default DB lives in project/data/, overridable via env.
DEFAULT_DB_PATH: Path = PROJECT_ROOT / "data" / "austria_jobs.db"

# Schema is co-located with the package so it ships in source control.
SCHEMA_PATH: Path = PACKAGE_ROOT / "schema.sql"

# Skill metadata lives next to the package so it can be copied into ~/.hermes/skills/.
SKILL_MD_PATH: Path = PROJECT_ROOT / "SKILL.md"


def db_path() -> Path:
    """Resolve the SQLite path from env or default."""
    p = os.environ.get("AUSTRIA_JOB_SCOUT_DB")
    return Path(p) if p else DEFAULT_DB_PATH


# ---------------------------------------------------------------------------
# Behavioural knobs — RESIDENTIAL IP SAFE BY DEFAULT
#
# The user's residential home IP is the scarcest resource in this project
# (see PITFALLS.md §"Pillar 0 — residential IP protection"). Defaults are
# tuned to stay under the noise floor of any WAF correlating per-household
# traffic patterns. Override ONLY when behind a proxy/VPN — set
# AGGRESSIVE_MODE=1 explicitly.
# ---------------------------------------------------------------------------

# Master safety switch. When False (default), the Fetcher enforces conservative
# budgets, refuses CF-protected sites, and adds long-tail random pauses.
# When True, the user has confirmed they are NOT on a residential IP
# (e.g. behind a rotating residential proxy, on a VPS, etc.) and the
# normal anti-bot-three-pillars budget applies.
AGGRESSIVE_MODE: bool = bool(int(os.environ.get("AUSTRIA_JOB_SCOUT_AGGRESSIVE", "0")))

# Per anti-bot-three-pillars Pillar 3 — random delays are mandatory. Long-tail
# distribution (most waits short, some long) is more human-like than uniform.
# On residential IP we use a heavier long tail. Ingestion (no network) skips.
DELAY_MIN_S: float = float(os.environ.get("AUSTRIA_JOB_SCOUT_DELAY_MIN", "5"))
DELAY_MAX_S: float = float(os.environ.get("AUSTRIA_JOB_SCOUT_DELAY_MAX", "25"))
LONG_PAUSE_PROB: float = float(os.environ.get("AUSTRIA_JOB_SCOUT_LONG_PAUSE", "0.15"))
LONG_PAUSE_MIN_S: float = float(os.environ.get("AUSTRIA_JOB_SCOUT_LONG_PAUSE_MIN", "60"))
LONG_PAUSE_MAX_S: float = float(os.environ.get("AUSTRIA_JOB_SCOUT_LONG_PAUSE_MAX", "180"))

# Daily request budget — the HARD ceiling. Residential IPs are valuable.
# Even with delays, volume correlates with detection.
DAILY_BUDGET_RESIDENTIAL: int = int(os.environ.get("AUSTRIA_JOB_SCOUT_DAILY_BUDGET", "50"))
DAILY_BUDGET_CF_SITE: int = int(os.environ.get("AUSTRIA_JOB_SCOUT_DAILY_BUDGET_CF", "10"))  # Cloudflare / Akamai / DataDome
DAILY_BUDGET_AGGRESSIVE: int = int(os.environ.get("AUSTRIA_JOB_SCOUT_DAILY_BUDGET_AGG", "500"))

def daily_budget() -> int:
    """Resolve the active daily request budget."""
    if AGGRESSIVE_MODE:
        return DAILY_BUDGET_AGGRESSIVE
    return DAILY_BUDGET_RESIDENTIAL

# Per-domain rate cap. Aggressive mode matches anti-bot-three-pillars standard.
# Residential mode is 3× slower so multiple domains don't spike together.
DEFAULT_RATE_PER_MIN: int = int(
    os.environ.get("AUSTRIA_JOB_SCOUT_RATE_PER_MIN", "3" if not AGGRESSIVE_MODE else "10")
)

# Per-domain circuit breaker — N consecutive failures → 24h cool-off.
CIRCUIT_BREAKER_THRESHOLD: int = int(os.environ.get("AUSTRIA_JOB_SCOUT_CB_THRESHOLD", "3"))
CIRCUIT_BREAKER_COOLDOWN_S: int = int(os.environ.get("AUSTRIA_JOB_SCOUT_CB_COOLDOWN", str(24 * 3600)))

# Dedupe TTL — same URL within this many seconds returns the cached RawResponse.
# Higher on residential mode so the same job-ad never gets re-fetched.
DEDUPE_TTL_S: int = int(
    os.environ.get("AUSTRIA_JOB_SCOUT_DEDUPE_TTL", str(7 * 24 * 3600) if not AGGRESSIVE_MODE else str(24 * 3600))
)

# ---------------------------------------------------------------------------
# Discrimination-before-fetch (PARAMOUNT, second constraint in the same family)
#
# Don't fetch hundreds of jobs at once. Filter and rank the candidate set
# BEFORE going to the network, so we only spend daily budget on postings
# that are plausibly relevant to the reference.
# ---------------------------------------------------------------------------

# Hard cap on fetches per single `pipeline` invocation. Even on aggressive
# mode, this is a sanity cap — bulk downloads require explicit override.
MAX_FETCH_PER_RUN: int = int(os.environ.get("AUSTRIA_JOB_SCOUT_MAX_PER_RUN", "25" if not AGGRESSIVE_MODE else "200"))
MAX_TARGETS_PER_RUN: int = int(os.environ.get("AUSTRIA_JOB_SCOUT_MAX_TARGETS", "30" if not AGGRESSIVE_MODE else "300"))

# Post-parse filter: a parsed JobPosting is only INDEXED if at least this
# fraction of the reference's skills appear in the posting. 0.2 = 20%.
# Below this, the posting is recorded in `austria_jobs` with status='skipped'
# so we can see it exists but don't burn similarity budget on it.
MIN_SKILL_OVERLAP_FOR_INDEX: float = float(os.environ.get("AUSTRIA_JOB_SCOUT_MIN_OVERLAP", "0.20"))

# Aggregator pagination: how many result pages we follow. karriere.at and
# similar rank by relevance; page 1 is what 95% of users would click.
# Don't paginate beyond this even in aggressive mode — re-rank locally.
MAX_AGGREGATOR_PAGES: int = int(os.environ.get("AUSTRIA_JOB_SCOUT_MAX_PAGES", "1" if not AGGRESSIVE_MODE else "3"))

# Top-N per source. After the source returns its ranked list, we only
# FETCH the top N — the rest are saved to the wishlist for later.
TOP_N_PER_SOURCE: int = int(os.environ.get("AUSTRIA_JOB_SCOUT_TOP_N", "10" if not AGGRESSIVE_MODE else "50"))

# When `pipeline` would have needed more fetches than MAX_FETCH_PER_RUN,
# save the remainder to the wishlist and continue tomorrow. Never silently
# expand the budget; never silently drop targets.
STAGGER_REMAINDER: bool = bool(int(os.environ.get("AUSTRIA_JOB_SCOUT_STAGGER", "1")))

# Source priority (lower = fetch first). Tier 1 = zero-stealth JSON endpoints,
# Tier 2 = sitemap + soft-anti-bot aggregators, Tier 3 = direct career pages,
# Tier 4 = WAF-protected (blocked in residential mode).
SOURCE_PRIORITY: dict[str, int] = {
    # Tier 1 — JSON/XML, no stealth, structured (zero detection risk)
    "ats_greenhouse":      10,
    "ats_lever":           10,
    "ats_smartrecruiters": 10,
    "ats_workable":        10,
    "ats_recruitee":       10,
    "ats_personio":        10,
    # Tier 2 — aggregator sitemap + search, soft anti-bot (low detection risk)
    "sitemap_xml":         20,
    "karriere_at":         25,
    "ams_ogd":             25,
    # Tier 3 — direct career pages of known companies (moderate risk)
    "career_path":         30,
    "rss":                 30,
    # Tier 4 — WAF-protected (BLOCKED in residential mode; only with proxy)
    "ats_workday":         90,
    "stepstone_at":        90,
    "willhaben":           90,
    "indeed_at":           90,
    "jobs_at":             90,
}

# Sites that are too dangerous from residential IP — BLOCKED unless AGGRESSIVE_MODE.
# These are WAF-protected (Cloudflare Bot Mgmt / Akamai / DataDome / Turnstile).
# Always use a proxy to scrape them. See PITFALLS.md §Pillar 9 + research/AUSTRIA_JOB_LANDSCAPE.md.
CF_PROTECTED_SITES: frozenset[str] = frozenset({
    "stepstone.at",         # Cloudflare Bot Management
    "indeed.com",           # Managed Challenge + content-signals
    "at.indeed.com",        # alias
    "myworkdayjobs.com",    # Cloudflare + Turnstile (Workday)
    "willhaben.at",         # DataDome-style fingerprinting
    "jobs.at",              # JS-rendered, fingerprinting observed
})

def is_cf_protected(url: str) -> bool:
    """True if the URL host is in the WAF-protected blocklist."""
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    # strip leading "www."
    if host.startswith("www."):
        host = host[4:]
    return host in CF_PROTECTED_SITES

# Similarity weights (must sum to 1.0). Override via env if experimenting.
SIM_WEIGHT_SEMANTIC: float = float(os.environ.get("AUSTRIA_JOB_SCOUT_W_SEM", "0.50"))
SIM_WEIGHT_KEYWORD: float = float(os.environ.get("AUSTRIA_JOB_SCOUT_W_FTS", "0.30"))
SIM_WEIGHT_SKILLS: float = float(os.environ.get("AUSTRIA_JOB_SCOUT_W_JAC", "0.20"))

# Embedding model. Local sentence-transformers is the v1 default (no API key needed).
EMBEDDING_MODEL: str = os.environ.get("AUSTRIA_JOB_SCOUT_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# Optional: cloud embedding override
EMBEDDING_API_KEY: str | None = os.environ.get("AUSTRIA_JOB_SCOUT_EMBED_API_KEY")
EMBEDDING_API_BASE: str | None = os.environ.get("AUSTRIA_JOB_SCOUT_EMBED_API_BASE")

# opendata.host — optional. If unset, company-status enrichment is skipped.
OPENDATA_API_KEY: str | None = os.environ.get("OPENDATA_API_KEY")
OPENDATA_HOST: str = os.environ.get("OPENDATA_HOST", "http://api.opendata.host")
# NOTE: opendata.host is HTTP not HTTPS — see PITFALLS.md pillar 4.

# Verbose logging
VERBOSE: bool = bool(int(os.environ.get("AUSTRIA_JOB_SCOUT_VERBOSE", "0")))


def assert_weights_sum_to_one() -> None:
    """Refuse to start if the similarity weights don't sum to 1.0 ± 0.01."""
    s = SIM_WEIGHT_SEMANTIC + SIM_WEIGHT_KEYWORD + SIM_WEIGHT_SKILLS
    if abs(s - 1.0) > 0.01:
        raise ValueError(
            f"Similarity weights must sum to 1.0; got {s:.3f} "
            f"(semantic={SIM_WEIGHT_SEMANTIC}, keyword={SIM_WEIGHT_KEYWORD}, skills={SIM_WEIGHT_SKILLS})"
        )
