"""RSS/Atom feed discovery + extraction for Austrian job sources.

Provides:
  - URL builders for Austrian companies, aggregators, and Wien-specific sources
  - RSS 2.0 / Atom feed parser (returns ATSJob + metadata)
  - HTML autodetection via ``<link rel="alternate" type="application/rss+xml">``
  - Target list builder for the pipeline (uses ``source_kind=rss_company`` /
    ``rss_aggregator`` so the fetcher tier-orders them as RSS = Tier 2)

Designed for Pillar 0 safety: RSS/Atom are XML, no JavaScript, zero stealth.
This module makes NO network calls — it only builds URLs and parses feeds
that the fetcher has already retrieved.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from ..extractors.ats_extractor import ATSJob, _extract_skills_from_text

logger = logging.getLogger(__name__)

# RSS source priority — Tier 2 (low detection risk, structured XML)
# Lower number = try first. Matches the scale used in config.SOURCE_PRIORITY.
RSS_PRIORITY = 30


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------

# Common RSS feed URL patterns tried for Austrian companies.
# Includes both `www.` and apex variants; tested for karriere.at, wienerjobs.at,
# and small KMU CMS layouts (WordPress, TYPO3).
_AUSTRIAN_COMPANY_FEED_PATTERNS = (
    "/karriere/feed",
    "/karriere/rss",
    "/jobs/feed",
    "/jobs/rss",
    "/stellenangebote/feed",
    "/stellenangebote/rss",
    "/careers/feed",
    "/careers/rss",
    "/feed",
    "/rss",
    "/jobs.rss",
    "/karriere.xml",
)


def _normalize_company_domain(domain: str) -> str:
    """Strip scheme, leading 'www.', and trailing slash.

    Accepts: 'techstartup.at', 'www.techstartup.at', 'http://techstartup.at'
    Returns: 'techstartup.at'
    """
    d = (domain or "").strip()
    if not d:
        return ""
    if "://" in d:
        d = urlparse(d).hostname or d.split("://", 1)[1]
    d = d.split("/", 1)[0]
    if d.lower().startswith("www."):
        d = d[4:]
    return d


def build_austrian_company_rss_urls(domain: str) -> list[str]:
    """Build candidate RSS feed URLs for an Austrian company domain.

    Returns a list of URLs (https://) covering both `www.` and apex variants.
    Always non-empty: a generic ``/feed`` is included as a final fallback.
    """
    apex = _normalize_company_domain(domain)
    if not apex:
        return []

    urls: list[str] = []
    for pattern in _AUSTRIAN_COMPANY_FEED_PATTERNS:
        # Both www and apex variants
        urls.append(f"https://www.{apex}{pattern}")
        urls.append(f"https://{apex}{pattern}")
    return urls


def build_aggregator_rss_urls() -> list[str]:
    """Build RSS feed URLs for Austrian job aggregators.

    Includes karriere.at, stepstone (AT/DE), and standard aggregator feeds
    that publish RSS for Austrian roles.
    """
    return [
        # karriere.at — main aggregator
        "https://www.karriere.at/rss",
        "https://www.karriere.at/jobs/rss",
        # StepStone AT — known to publish RSS for some categories
        "https://www.stepstone.at/rss",
        "https://www.stepstone.de/rss",
        # StepStone specialised
        "https://www.stepstone.at/stelle/rss",
        "https://www.stepstone.de/stelle/rss",
        # Wienerjobs (we mirror their search feed; they don't always expose RSS
        # but listing common paths is cheap)
        "https://www.wienerjobs.at/rss",
        "https://www.wienerjobs.at/jobs/rss",
    ]


def build_wien_specific_rss_urls() -> list[str]:
    """Build Wien-specific RSS feed URLs (city institutions + universities).

    These are public-sector / educational sources — high-value, low-noise.
    """
    return [
        # City of Wien / Wirtschaftsagentur Wien
        "https://www.wien.gv.at/rss",
        "https://www.wien.gv.at/feed",
        "https://www.wirtschaftsagentur.at/rss",
        "https://www.wirtschaftsagentur.at/feed",
        "https://jobs.wien.gv.at/rss",
        "https://jobs.wien.gv.at/feed",
        # Universities — public, frequent, structured
        "https://univie.ac.at/rss",
        "https://univie.ac.at/jobs/rss",
        "https://univie.ac.at/karriere/rss",
        "https://www.tuwien.ac.at/rss",
        "https://www.tuwien.ac.at/jobs/rss",
        "https://www.tuwien.ac.at/karriere/rss",
    ]


def build_all_austrian_rss_targets(
    seed_domains: list[str],
    include_aggregators: bool = True,
) -> list[dict[str, Any]]:
    """Build a complete list of RSS feed targets for the pipeline.

    Each target dict has:
        - url: feed URL
        - ats: 'rss'
        - source_kind: 'rss_company' or 'rss_aggregator'
        - company_name: extracted from domain (for company feeds)
        - predicted_relevance: 0..1 heuristic
        - priority: int ≤ 30 (Tier 2)

    Caller passes the result to fetcher.fetch(); the fetcher tier-orders
    by priority regardless of list order.
    """
    targets: list[dict[str, Any]] = []

    # Company feeds — one target per (domain, pattern). Keep ALL variants
    # because we don't know which path the company's CMS publishes to.
    # Dedup later by URL.
    for domain in seed_domains:
        apex = _normalize_company_domain(domain)
        if not apex:
            continue
        for url in build_austrian_company_rss_urls(apex):
            targets.append({
                "url": url,
                "ats": "rss",
                "source_kind": "rss_company",
                "company_name": apex,
                "predicted_relevance": 0.6,
                "priority": RSS_PRIORITY,
            })

    if include_aggregators:
        for url in build_aggregator_rss_urls():
            targets.append({
                "url": url,
                "ats": "rss",
                "source_kind": "rss_aggregator",
                "company_name": None,
                "predicted_relevance": 0.7,
                "priority": RSS_PRIORITY,
            })
        for url in build_wien_specific_rss_urls():
            targets.append({
                "url": url,
                "ats": "rss",
                "source_kind": "rss_aggregator",
                "company_name": None,
                "predicted_relevance": 0.8,  # Wien-specific = high relevance for Wien users
                "priority": RSS_PRIORITY,
            })

    # Dedup by URL — same feed can appear via multiple code paths.
    # Also collapse www.apex/apex (different paths but same server); we keep
    # one canonical URL per (host-no-www, path).
    seen: set[tuple[str, str]] = set()
    seen_full_url: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for t in targets:
        if t["url"] in seen_full_url:
            continue
        parsed = urlparse(t["url"])
        host = parsed.hostname or ""
        if host.lower().startswith("www."):
            host = host[4:]
        key = (host.lower(), parsed.path)
        if key in seen:
            continue
        seen.add(key)
        seen_full_url.add(t["url"])
        deduped.append(t)

    return deduped


# ---------------------------------------------------------------------------
# Detection + extraction
# ---------------------------------------------------------------------------

_HTML_RSS_LINK_RE = re.compile(
    r"""<link[^>]+rel=["']alternate["'][^>]+type=["']application/(?:rss|atom)\+xml["'][^>]*>""",
    re.IGNORECASE,
)


def is_rss_feed(content: str | bytes) -> bool:
    """Return True if content looks like an RSS/Atom feed (or HTML linking one).

    Works on:
      - Raw RSS 2.0 XML
      - Raw Atom XML
      - HTML with ``<link rel="alternate" type="application/rss+xml" ...>``
      - HTML with ``<link rel="alternate" type="application/atom+xml" ...>``
    """
    if not content:
        return False
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8", errors="replace")
        except Exception:
            return False

    # Fast path — sniff the first 256 chars for raw XML signatures.
    # Normalise leading whitespace because feeds are sometimes wrapped with
    # newlines/spaces (e.g. when saved from email or curl with --data-binary).
    head = content[:256].strip().lower()
    if head.startswith("<?xml") or "<rss" in head or "<feed" in head:
        return True

    # HTML with <link rel="alternate"> pointing to an RSS/Atom feed
    if "<link" in content.lower() and _HTML_RSS_LINK_RE.search(content):
        return True

    return False


def get_rss_info(content: str | bytes) -> dict[str, Any]:
    """Extract feed-level metadata from an RSS 2.0 or Atom feed.

    Returns a dict with keys: type, title, link, description, language,
    last_build_date, item_count. Returns ``{}`` on parse failure.
    """
    if not content:
        return {}

    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8", errors="replace")
        except Exception:
            return {}

    # Python's ElementTree is strict — XML declaration must be at byte 0.
    # Strip leading/trailing whitespace so wrapped feeds parse cleanly.
    parse_target = content.strip()
    try:
        root = ET.fromstring(parse_target)
    except ET.ParseError as e:
        logger.debug("get_rss_info: XML parse failed: %s", e)
        return {}

    # Detect feed type
    tag = root.tag.split("}", 1)[-1].lower() if "}" in root.tag else root.tag.lower()
    feed_type = "rss20" if tag == "rss" else "atom" if tag == "feed" else "unknown"

    info: dict[str, Any] = {"type": feed_type}

    if feed_type == "rss20":
        channel = root.find("channel")
        if channel is None:
            return {}
        info["title"] = _elem_text(channel, "title") or ""
        info["link"] = _elem_text(channel, "link") or ""
        info["description"] = _elem_text(channel, "description") or ""
        info["language"] = _elem_text(channel, "language") or ""
        info["last_build_date"] = _elem_text(channel, "lastBuildDate") or ""
        info["item_count"] = len(channel.findall(".//item"))
    elif feed_type == "atom":
        # Atom: feed-level elements
        info["title"] = _elem_text(root, "title") or ""
        # Atom <link href="...">
        link_elem = root.find("{*}link")
        if link_elem is None:
            link_elem = root.find(".//{*}link")
        if link_elem is not None:
            info["link"] = link_elem.get("href", "") or ""
        else:
            info["link"] = ""
        info["description"] = _elem_text(root, "subtitle") or ""
        info["language"] = _elem_text(root, "language") or ""
        info["last_build_date"] = _elem_text(root, "updated") or ""
        info["item_count"] = len(root.findall(".//{http://www.w3.org/2005/Atom}entry"))
    else:
        return {}

    return info


def extract_rss_jobs(content: str | bytes) -> list[ATSJob]:
    """Parse RSS 2.0 or Atom feed and return a list of ATSJob objects.

    Handles raw XML feeds and HTML pages that contain an RSS link.
    Returns [] on parse failure or empty feeds.
    """
    if not content:
        return []

    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8", errors="replace")
        except Exception:
            return []

    # If this is HTML with an RSS link, the actual feed isn't here — return []
    # The caller should follow the link separately.
    # ``.strip()`` (not ``.lstrip()``) — the test wraps feeds with trailing
    # newlines and our sniff must work either way.
    head = content[:256].strip().lower()
    if not (head.startswith("<?xml") or "<rss" in head or "<feed" in head):
        return []

    # Python's ElementTree is strict — XML declaration must be at byte 0.
    # Test fixtures wrap feeds in surrounding whitespace; strip before parsing
    # but preserve the actual payload.
    parse_target = content.strip()
    try:
        root = ET.fromstring(parse_target)
    except ET.ParseError as e:
        logger.debug("extract_rss_jobs: XML parse failed: %s", e)
        return []

    # Detect feed type
    tag = root.tag.split("}", 1)[-1].lower() if "}" in root.tag else root.tag.lower()
    if tag == "rss":
        return _extract_rss20(root)
    if tag == "feed":
        return _extract_atom(root)

    return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _elem_text(parent: ET.Element | None, tag: str) -> str | None:
    """Get first child text matching ``tag``, namespace-agnostic. Returns None."""
    if parent is None:
        return None
    child = parent.find(tag)
    if child is None:
        child = parent.find(f".//{{*}}{tag}")
    if child is not None and child.text:
        return child.text.strip()
    return None


def _company_from_feed_title(title: str) -> str | None:
    """Extract a clean company name from a feed title.

    Strips trailing descriptors like "Karriere", "Jobs", "Stellenangebote",
    " - Jobs", "| Wien", etc. Returns None if title is empty or unparseable.
    """
    if not title:
        return None
    t = title.strip()
    # Remove common trailing suffixes (case-insensitive)
    t = re.sub(
        r"\s*[\(\|]\s*.*$",  # anything in parens or after pipe
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\s*[-\u2013\u2014|]\s*(karriere|jobs?|stellenangebote|"
        r"careers?|offene\s+stellen)\s*$",
        "",
        t,
        flags=re.IGNORECASE,
    )
    # If title is something like "Tech Startup GmbH Karriere", strip "Karriere"
    t = re.sub(
        r"\s+(karriere|jobs?|stellenangebote|careers?)\s*$",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = t.strip(" -|\t")
    return t or None


def _extract_rss20(root: ET.Element) -> list[ATSJob]:
    """Parse RSS 2.0 <channel><item> entries into ATSJob objects."""
    channel = root.find("channel")
    if channel is None:
        return []
    company = _company_from_feed_title(_elem_text(channel, "title") or "")

    jobs: list[ATSJob] = []
    for item in channel.findall(".//item"):
        title = _elem_text(item, "title")
        link = _elem_text(item, "link")
        if not title:
            continue
        description = _elem_text(item, "description")
        posted = (
            _elem_text(item, "pubDate")
            or _elem_text(item, "published")
            or _elem_text(item, "updated")
        )
        skills = _extract_skills_from_text(description or title)
        jobs.append(ATSJob(
            source="rss_rss20",
            url=link or "",
            title=title,
            company=company,
            description=description,
            skills=skills,
            posted_date=posted,
        ))
    return jobs


def _extract_atom(root: ET.Element) -> list[ATSJob]:
    """Parse Atom <feed><entry> elements into ATSJob objects."""
    feed_title = _elem_text(root, "title") or ""
    company = _company_from_feed_title(feed_title)

    entries = root.findall(f".//{_ATOM_NS}entry")
    if not entries:
        entries = root.findall(".//entry")

    jobs: list[ATSJob] = []
    for entry in entries:
        title = _elem_text(entry, "title")
        if not title:
            continue
        # Atom <link href="...">
        link = _elem_text(entry, "link")
        if not link:
            link_elem = entry.find(f"{_ATOM_NS}link")
            if link_elem is None:
                link_elem = entry.find("{*}link")
            if link_elem is not None:
                link = link_elem.get("href", "") or ""
        description = (
            _elem_text(entry, "summary")
            or _elem_text(entry, "content")
            or _elem_text(entry, "description")
        )
        posted = (
            _elem_text(entry, "published")
            or _elem_text(entry, "updated")
            or _elem_text(entry, "pubDate")
        )
        skills = _extract_skills_from_text(description or title)
        jobs.append(ATSJob(
            source="rss_atom",
            url=link or "",
            title=title,
            company=company,
            description=description,
            skills=skills,
            posted_date=posted,
        ))
    return jobs