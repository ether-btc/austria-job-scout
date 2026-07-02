"""Enhanced RSS feed discovery and extraction for Austrian job sites.

Builds on the aggregator_search module but focuses specifically on RSS/Atom
feeds, which are an underutilized source of structured job data for Austrian
companies and job boards.

RSS/Advantages:
  - Zero stealth required — XML is text
  - Structured data (title, link, description, pubDate)
  - Many Austrian KMU publish feeds at /karriere/feed, /jobs/rss
  - Cloudflare-friendly (XML, no JavaScript)
  - Can be cached long-term for residential safety

This module provides:
  1. URL builders for common RSS patterns
  2. XML parsing that handles both RSS 2.0 and Atom
  3. Integration with ATSJob data structure
  4. Rate-limiting recommendations for safe fetching

Pillar 0-compliant: pure function, no network calls.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

from austria_job_scout.extractors.ats_extractor import ATSJob, _extract_skills_from_text

logger = logging.getLogger(__name__)


@dataclass
class RssJob:
    """Parsed job from an RSS/Atom feed."""
    title: str                # Job title
    link: str                 # Job URL (absolute)
    description: str | None   # Job description
    pub_date: str | None     # Publication date
    category: str | None     # Job category
    source: str              # Feed provenance
    feed_url: str           # The feed this came from
    feed_title: str | None   # The feed's title


# ---------------------------------------------------------------------------
# RSS URL builders for Austrian sources
# ---------------------------------------------------------------------------



def _extract_clean_company_name(feed_title: str, feed_description: str | None = None, url: str | None = None) -> str | None:
    """Extract clean company name from RSS feed metadata."""
    if not feed_title:
        return None
    
    # Strategy 1: Strip common career suffixes from feed title
    clean_title = feed_title.strip()
    
    # Remove common German career suffixes
    for suffix in [' Karriere', ' Stellen', ' Jobs', ' Career', ' Recruitment']:
        if clean_title.endswith(suffix):
            clean_title = clean_title[:-len(suffix)]
    
    # Strategy 2: If still has trailing words that aren't company-like, try description
    if len(clean_title.split()) > 3 and feed_description:
        # Try to find company name in description
        desc_words = feed_description.split()
        if len(desc_words) >= 2:
            # Take first few words from description as company name
            clean_title = ' '.join(desc_words[:3])
    
    # Strategy 3: Extract from URL domain
    if url and not clean_title:
        import re
        match = re.search(r'https?://([^/]+)', url)
        if match:
            domain = match.group(1)
            # Remove TLD and common suffixes
            clean_title = domain.replace('.at', '').replace('.com', '').replace('.de', '')
    
    # Final cleanup
    clean_title = clean_title.strip()
    
    # Only return if it looks like a company name (not empty, not just common words)
    if (clean_title and 
        len(clean_title) > 2 and 
        not all(word in ['die', 'der', 'das', 'the', 'for', 'at', 'in'] for word in clean_title.lower().split())):
        return clean_title
    
    return None


def build_austrian_company_rss_urls(seed: str) -> list[str]:
    """Build common RSS URLs for Austrian companies.

    Args:
        seed: Company domain name (e.g., "bitpanda.com")

    Returns:
        List of candidate RSS URLs to probe
    """
    domain = seed.lower().strip()
    if domain.startswith("www."):
        domain = domain[4:]

    urls = []
    base_scheme = "https"

    # Common RSS feed patterns for Austrian companies
    rss_patterns = [
        f"{base_scheme}://{domain}/karriere/feed",
        f"{base_scheme}://{domain}/jobs/feed",
        f"{base_scheme}://{domain}/stellenangebote/rss",
        f"{base_scheme}://{domain}/stellen/rss",
        f"{base_scheme}://{domain}/karriere/rss",
        f"{base_scheme}://{domain}/jobs/rss",
        f"{base_scheme}://{domain}/feed/jobs",
        f"{base_scheme}://{domain}/careers/feed",
        f"{base_scheme}://{domain}/jobs/atom.xml",
        f"{base_scheme}://{domain}/karriere/atom.xml",
        f"{base_scheme}://{domain}/jobs.xml",
        f"{base_scheme}://{domain}/karriere.xml",
    ]

    # Add www variants
    for url in rss_patterns:
        if not url.startswith("https://www."):
            urls.append(url.replace(f"{base_scheme}://{domain}", f"{base_scheme}://www.{domain}"))

    return urls


def build_aggregator_rss_urls() -> list[str]:
    """Build RSS URLs for major Austrian job aggregators.

    These are public, RSS-friendly sources that can be safely scraped
    from residential IP (no Cloudflare).
    """
    return [
        # karriere.at (public RSS)
        "https://www.karriere.at/static/sitemaps/sitemap-jobs-https.xml",
        
        # Alternative sitemap approach
        "https://www.karriere.at/sitemap.xml",
        "https://www.karriere.at/jobs/rss",
        
        # willhaben.at (careers section - may be blocked)
        "https://www.willhaben.at/rss/careers",
        
        # Stepstone Austria (RSS exists but may be blocked)
        "https://www.stepstone.at/rss/jobs/rss.xml",
        
        # AMS Austria public sector
        "https://jobs.ams.at/rss.xml",  # May be behind authentication
        
        # German aggregators with Austrian coverage
        "https://www.stepstone.de/rss/rss-feeds/fachbereich-it-jobs.xml",
        "https://www.stepstone.de/rss/rss-feeds/standort-oesterreich.xml",
    ]


def build_wien_specific_rss_urls() -> list[str]:
    """Build RSS URLs for Wien-specific job sources."""
    return [
        # Wien-specific sources
        "https://wirtschaftsagentur.at/feeds/jobs.xml",
        "https://wien.gv.at/rss/jobs",
        "https://wien.gv.at/stellenangebote/feed",
        "https://wien.gv.at/karriere/rss",
        
        # General Wien business feeds (may include jobs)
        "https://wien.at/rss/unternehmen",
        "https://wien.at/rss/wirtschaft",
        
        # University Wien job feeds
        "https://jobs.univie.ac.at/rss",
        "https://jobs.tuwien.ac.at/rss",
    ]


# ---------------------------------------------------------------------------
# RSS/Atom feed extraction
# ---------------------------------------------------------------------------


def extract_rss_jobs(xml_text: str | bytes, feed_url: str = "") -> list[ATSJob]:
    """Extract jobs from any RSS 2.0 or Atom feed.

    Args:
        xml_text: Raw XML content of the feed
        feed_url: Source URL for metadata/debugging

    Returns:
        List of ATSJob objects with RSS data mapped appropriately
    """
    if isinstance(xml_text, bytes):
        xml_text = xml_text.decode("utf-8", errors="replace")

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.debug("RSS extraction failed for %s: %s", feed_url, e)
        return []

    # Normalize feed type
    root_tag = root.tag.lower()
    if root_tag.endswith("rss"):
        return _extract_rss20_jobs(root, feed_url)
    elif root_tag.endswith("feed"):
        return _extract_atom_jobs(root, feed_url)
    else:
        # Unknown format, try RSS as fallback
        return _extract_rss20_jobs(root, feed_url)


def _extract_rss20_jobs(root: ET.Element, feed_url: str) -> list[ATSJob]:
    """Extract jobs from RSS 2.0 feed."""
    jobs: list[ATSJob] = []

    # RSS 2.0 namespace (often omitted but let's be safe)
    ns = {}
    if root.tag.startswith("{"):
        ns["rss"] = root.tag.split("}")[0][1:]

    # Find channel
    channel = root.find("channel", ns)
    if channel is None:
        return []

    # Get feed title for source tracking
    feed_title = channel.findtext("title", "", ns)

    # Process items
    for item in channel.findall("item", ns):
        rss_job = _parse_rss_item(item, ns)
        if rss_job:
            # Map to ATSJob
            ats_job = ATSJob(
                source=f"rss_{rss_job.source}",
                url=rss_job.link,
                title=rss_job.title,
                company=_extract_clean_company_name(feed_title, feed_description, feed_url),
                description=rss_job.description,
                posted_date=rss_job.pub_date,
                skills=_extract_skills_from_text(rss_job.description or rss_job.title),
            )
            jobs.append(ats_job)

    return jobs


def _extract_atom_jobs(root: ET.Element, feed_url: str) -> list[ATSJob]:
    """Extract jobs from Atom feed."""
    jobs: list[ATSJob] = []

    # Atom namespace (required)
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    # Get feed title
    feed_title = root.findtext("title", "", ns)

    # Process entries
    for entry in root.findall("entry", ns):
        rss_job = _parse_atom_entry(entry, ns)
        if rss_job:
            # Map to ATSJob
            ats_job = ATSJob(
                source=f"atom_{rss_job.source}",
                url=rss_job.link,
                title=rss_job.title,
                company=_extract_clean_company_name(feed_title, feed_description, feed_url),
                description=rss_job.description,
                posted_date=rss_job.pub_date,
                skills=_extract_skills_from_text(rss_job.description or rss_job.title),
            )
            jobs.append(ats_job)

    return jobs


def _parse_rss_item(item: ET.Element, ns: dict) -> RssJob | None:
    """Parse RSS 2.0 item into RssJob."""
    title = item.findtext("title", "", ns)
    if not title or len(title.strip()) < 3:
        return None

    link = item.findtext("link", "", ns)
    if not link:
        # Some RSS feeds use guid instead
        link = item.findtext("guid", "", ns)

    description = item.findtext("description", "", ns)
    pub_date = item.findtext("pubDate", "", ns)

    # Extract category
    category_elem = item.find("category", ns)
    category = category_elem.text if category_elem is not None else None

    return RssJob(
        title=title.strip(),
        link=link.strip() if link else "",
        description=description.strip() if description else None,
        pub_date=pub_date.strip() if pub_date else None,
        category=category,
        source="rss20",
        feed_url="",
        feed_title="",
    )


def _parse_atom_entry(entry: ET.Element, ns: dict) -> RssJob | None:
    """Parse Atom entry into RssJob."""
    title = entry.findtext("title", "", ns)
    if not title or len(title.strip()) < 3:
        return None

    # Atom <link> has href attribute
    link_elem = entry.find("link", ns)
    link = link_elem.get("href") if link_elem is not None else ""

    # Atom may have summary instead of description
    description = entry.findtext("summary", "", ns) or entry.findtext("content", "", ns)

    # Atom has published/updated dates
    pub_date = entry.findtext("published", "", ns) or entry.findtext("updated", "", ns)

    # Atom may have category
    category_elem = entry.find("category", ns)
    category = category_elem.get("term") if category_elem is not None else None

    return RssJob(
        title=title.strip(),
        link=link.strip() if link else "",
        description=description.strip() if description else None,
        pub_date=pub_date.strip() if pub_date else None,
        category=category,
        source="atom",
        feed_url="",
        feed_title="",
    )


# ---------------------------------------------------------------------------
# Feed discovery (probing URLs)
# ---------------------------------------------------------------------------


def probe_rss_feed_url(url: str) -> list[RssJob] | None:
    """
    Probe a single URL to see if it contains RSS/Atom feed content.
    Returns None if not a feed, otherwise list of RssJob objects.

    This is for the caller to implement network fetching with proper
    residential IP safety measures.
    """
    # This is a placeholder - the actual network probing should be done
    # by the fetcher module with appropriate delays and rate limiting.
    # Here we just provide the parsing logic.
    pass


# ---------------------------------------------------------------------------
# Feed utilities
# ---------------------------------------------------------------------------


def get_rss_info(xml_text: str | bytes) -> dict[str, Any]:
    """Extract metadata from an RSS/Atom feed."""
    if isinstance(xml_text, bytes):
        xml_text = xml_text.decode("utf-8", errors="replace")

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}

    info = {
        "type": "unknown",
        "title": "",
        "link": "",
        "description": "",
        "language": "",
        "last_build_date": "",
        "item_count": 0,
    }

    root_tag = root.tag.lower()
    if root_tag.endswith("rss"):
        info["type"] = "rss20"
        channel = root.find("channel")
        if channel is not None:
            info["title"] = channel.findtext("title", "")
            info["link"] = channel.findtext("link", "")
            info["description"] = channel.findtext("description", "")
            info["language"] = channel.findtext("language", "")
            info["last_build_date"] = channel.findtext("lastBuildDate", "")
            info["item_count"] = len(channel.findall("item"))
    elif root_tag.endswith("feed"):
        info["type"] = "atom"
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        info["title"] = root.findtext("title", "", ns)
        info["description"] = root.findtext("subtitle", "", ns)
        link_elem = root.find("link", ns)
        if link_elem is not None:
            info["link"] = link_elem.get("href", "")
        info["language"] = root.get("xml:lang", "")
        info["last_build_date"] = root.findtext("updated", "", ns)
        info["item_count"] = len(root.findall("entry", ns))

    return info


def is_rss_feed(html_text: str | bytes) -> bool:
    """Quick check if content looks like RSS/Atom."""
    if isinstance(html_text, bytes):
        peek = html_text[:500].decode("utf-8", errors="replace")
    else:
        peek = html_text[:500]

    # RSS indicators
    rss_indicators = [
        "<?xml",
        "<rss",
        "<feed",
        "<channel",
        "<item",
        "<entry",
        "<title",
    ]

    return any(indicator.lower() in peek.lower() for indicator in rss_indicators)


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------


def build_all_austrian_rss_targets(seed_domains: list[str], include_aggregators: bool = True) -> list[dict]:
    """Build a complete list of RSS targets for Austrian job discovery.

    Args:
        seed_domains: List of company domains to probe for RSS feeds
        include_aggregators: Whether to include general aggregator feeds

    Returns:
        List of target dicts compatible with the orchestrator format
    """
    targets = []

    # Add company-specific RSS feeds
    for domain in seed_domains:
        rss_urls = build_austrian_company_rss_urls(domain)
        for url in rss_urls:
            targets.append({
                "ats": "rss_feed",
                "source_kind": "rss_company",
                "url": url,
                "predicted_relevance": 0.6,  # RSS feeds are reliable
                "priority": 25,  # Tier 2 - good quality
                "company_domain": domain,
                "notes": "Austrian company RSS feed",
            })

    # Add aggregator feeds
    if include_aggregators:
        aggregator_urls = build_aggregator_rss_urls() + build_wien_specific_rss_urls()
        for url in aggregator_urls:
            targets.append({
                "ats": "rss_feed",
                "source_kind": "rss_aggregator",
                "url": url,
                "predicted_relevance": 0.5,  # Broad coverage
                "priority": 30,  # Tier 3 - broader scope
                "notes": "Austrian job aggregator RSS",
            })

    return targets