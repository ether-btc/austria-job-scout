"""Aggregator search URL builders — pure functions, no network.

Builds the search URLs that the fetcher will hit, given a ReferenceJob.
These are the SAME URLs a human would type into the search box; we just
assemble them from the parsed reference.

Sources covered:
  - karriere.at (largest private AT board, soft anti-bot)
  - jobs.at (medium)
  - AMS jobboerse.gv.at (government, polite scraper)

NOT covered here (BLOCKED residential, see config.CF_PROTECTED_SITES):
  - stepstone.at — Cloudflare Bot Mgmt
  - indeed.at   — Managed Challenge
  - jobs.at     — JS-render + fingerprinting (degraded but possible in aggressive mode)
  - willhaben.at — DataDome

A target with `predicted_relevance` and `priority` is attached to each URL
so the fetcher can sort them.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlencode

from ..config import SOURCE_PRIORITY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(s: str) -> str:
    """Lowercase, ascii-only, hyphens. For karriere.at URL slugs."""
    s = s.lower()
    s = re.sub(r"ä", "ae", s)
    s = re.sub(r"ö", "oe", s)
    s = re.sub(r"ü", "ue", s)
    s = re.sub(r"ß", "ss", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _encoded_query(s: str) -> str:
    """URL-encode for query string. Spaces → '+' (karriere.at style)."""
    from urllib.parse import quote_plus
    return quote_plus(s, safe="")


# ---------------------------------------------------------------------------
# karriere.at
# ---------------------------------------------------------------------------

def karriere_at_search_urls(reference) -> list[dict]:
    """Build karriere.at search URLs for a ReferenceJob.

    Returns up to 3 URLs (one per language variant from language_queries):
      - de query (or fallback to role)
      - en query (or fallback)
      - role-only (no location filter)
    """
    out: list[dict] = []
    lang_queries = getattr(reference, "language_queries", {}) or {}
    role = getattr(reference, "role_query", None) or getattr(reference, "title", None) or ""
    location = getattr(reference, "location", None) or "Wien"   # AT default

    seen_urls: set[str] = set()
    for lang_key in ("de", "en"):
        q = lang_queries.get(lang_key) or role
        if not q:
            continue
        params = {"q": q}
        if location:
            params["location"] = location
        # Build URL manually so we control the encoding (karriere.at style).
        from urllib.parse import quote_plus
        url = (
            "https://www.karriere.at/jobs?"
            + "&".join(f"{k}={quote_plus(str(v), safe='')}" for k, v in params.items())
        )
        if url in seen_urls:
            continue
        seen_urls.add(url)
        out.append({
            "ats": "karriere_at",
            "source_kind": "aggregator_query",
            "url": url,
            "predicted_relevance": 0.7,
            "priority": SOURCE_PRIORITY.get("karriere_at", 25),
            "language": lang_key,
            "role_query": q,
            "notes": "aggregator search; karriere.at soft anti-bot, no stealth required",
        })

    # Always include a role-only search (broader recall, lower relevance)
    if role:
        from urllib.parse import quote_plus
        url = f"https://www.karriere.at/jobs?q={quote_plus(role, safe='')}"
        if url not in seen_urls:
            out.append({
                "ats": "karriere_at",
                "source_kind": "aggregator_query",
                "url": url,
                "predicted_relevance": 0.5,
                "priority": SOURCE_PRIORITY.get("karriere_at", 25),
                "language": "de",
                "role_query": role,
                "notes": "aggregator search; role-only, no location",
            })

    return out


def karriere_at_sitemap_url() -> str:
    """The public jobs sitemap — a single fetch returns all current AT jobs."""
    return "https://www.karriere.at/static/sitemaps/sitemap-jobs-https.xml"


# ---------------------------------------------------------------------------
# jobs.at (Tier-2; degraded — JS-rendered, headless browser preferred in v2)
# ---------------------------------------------------------------------------

def jobs_at_search_urls(reference) -> list[dict]:
    """Build jobs.at search URLs. jobs.at is JS-rendered so the fetcher will
    likely get a shell page; the real listings come from their API at
    ``/restapi/job/list`` (we'll wire that in iter-3 if useful)."""
    role = getattr(reference, "role_query", None) or getattr(reference, "title", None) or ""
    if not role:
        return []
    from urllib.parse import quote_plus
    return [{
        "ats": "jobs_at",
        "source_kind": "aggregator_query",
        "url": f"https://www.jobs.at/?q={quote_plus(role, safe='')}",
        "predicted_relevance": 0.4,
        "priority": SOURCE_PRIORITY.get("jobs_at", 90),   # Tier-4 default; flagged
        "language": "de",
        "role_query": role,
        "notes": "WAF-protected; only safe in AGGRESSIVE_MODE with proxy",
    }]


# ---------------------------------------------------------------------------
# AMS / jobboerse.gv.at (public sector)
# ---------------------------------------------------------------------------

def ams_search_urls(reference) -> list[dict]:
    """AMS eJob-Room — Austria's public employment service.

    Free, OGD data feeds exist via data.gv.at. v1 just points at the search
    page; iter-3 will wire the JSON feed."""
    role = getattr(reference, "role_query", None) or getattr(reference, "title", None) or ""
    if not role:
        return []
    from urllib.parse import quote_plus
    return [{
        "ats": "ams_ogd",
        "source_kind": "aggregator_query",
        "url": f"https://www.ams.at/jobroom/list?keyword={quote_plus(role, safe='')}",
        "predicted_relevance": 0.5,
        "priority": SOURCE_PRIORITY.get("ams_ogd", 25),
        "language": "de",
        "role_query": role,
        "notes": "AMS public job board; polite scraping permitted",
    }] 


# ---------------------------------------------------------------------------
# Aggregator dispatch
# ---------------------------------------------------------------------------

def all_aggregator_targets(reference) -> list[dict]:
    """All aggregator targets for a reference. Sorted by predicted_relevance."""
    out: list[dict] = []
    out.extend(karriere_at_search_urls(reference))
    out.extend(jobs_at_search_urls(reference))
    out.extend(ams_search_urls(reference))
    out.sort(key=lambda t: (-t["predicted_relevance"], t["priority"]))
    return out
