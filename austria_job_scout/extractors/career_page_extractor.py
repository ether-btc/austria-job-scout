"""Career page extractor — multi-job extraction from generic company career pages.

Most Austrian KMU career pages fall into one of these categories:

1. **JSON-LD JobPosting** — schema.org/JobPosting blocks embedded in
   ``<script type="application/ld+json">``. This is the gold standard:
   structured, zero-guesswork, works for any CMS that supports schema.org
   (WordPress, TYPO3, Drupal, custom).

2. **RSS/Atom feed** — companies that publish ``/karriere/feed`` or
   ``/jobs/rss``. XML, structured, zero-stealth.

3. **Link-based extraction** — fallback: scan HTML for ``<a>`` tags whose
   href contains ``/job/`` or ``/stellenangebote/`` and extract the link
   text as a job title. Last resort; noisy but catches the long tail.

This module provides ``extract_career_page_jobs()`` which tries all three
strategies in order and returns a flat list of ATSJob objects.

Designed to be Pillar 0-compliant: the pipeline already fetched the page
body, so this function does NO network calls. It parses pre-fetched HTML.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from .ats_extractor import (
    ATSJob,
    _extract_skills_from_text,
    _is_remote,
    extract_json_ld,
    parse_json_ld_job,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strategy 1: JSON-LD JobPosting extraction (primary)
# ---------------------------------------------------------------------------


def extract_json_ld_jobs(html: str | bytes) -> list[ATSJob]:
    """Extract ALL JobPosting JSON-LD blocks from an HTML page.

    Unlike ``ats_extractor.extract_from_html()`` which returns only the
    first match, this returns every JobPosting found — suitable for career
    listing pages that list multiple positions.
    """
    json_ld_blocks = extract_json_ld(html)
    jobs: list[ATSJob] = []
    
    for block in json_ld_blocks:
        try:
            job = parse_json_ld_job(block)
            if job is not None:
                job.source = "career_json_ld"
                jobs.append(job)
        except Exception as e:
            logger.warning("Failed to parse JSON-LD job: %s", e)
            continue
            
    return jobs


# ---------------------------------------------------------------------------
# Strategy 2: RSS/Atom feed extraction
# ---------------------------------------------------------------------------


def extract_rss_feed(xml_text: str | bytes) -> list[ATSJob]:
    """Extract jobs from an RSS 2.0 or Atom XML feed.

    Many Austrian companies publish job feeds at ``/karriere/feed`` or
    ``/jobs/rss``. The feed items contain ``<title>``, ``<link>``, and
    ``<description>`` — enough for a first-pass ATSJob.

    Handles both:
      - RSS 2.0: ``<rss><channel><item>...``
      - Atom: ``<feed><entry>...``
    """
    if isinstance(xml_text, bytes):
        xml_text = xml_text.decode("utf-8", errors="replace")

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("Failed to parse RSS/XML: %s", e)
        return []

    # Strip XML namespaces for simpler matching
    jobs: list[ATSJob] = []

    # RSS 2.0: <rss><channel><item>
    items = root.findall(".//item")
    if not items:
        # Atom: <feed><entry>
        try:
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        except ET.ParseError:
            # Try namespace-agnostic
            items = root.findall(".//entry")
        if not items:
            # Fallback: find any element that might contain job data
            items = root.findall("./*")

    for item in items:
        try:
            job = _parse_rss_item(item)
            if job:
                jobs.append(job)
        except Exception as e:
            logger.warning("Failed to parse RSS item: %s", e)
            continue

    return jobs


def _xml_text_nons(elem: ET.Element, tag: str) -> str | None:
    """Get child text, namespace-agnostic."""
    # Direct tag
    child = elem.find(tag)
    if child is None:
        # Namespace wildcard
        child = elem.find(f".//{{*}}{tag}")
    if child is not None and child.text:
        return child.text.strip()
    return None


def _parse_rss_item(elem: ET.Element) -> ATSJob | None:
    """Parse one <item> or <entry> element into an ATSJob."""
    title = _xml_text_nons(elem, "title")
    link = _xml_text_nons(elem, "link")
    if not link:
        # Atom <link href="...">
        link_elem = elem.find("{*}link")
        if link_elem is not None:
            link = link_elem.get("href", "")

    if not title:
        return None

    description = _xml_text_nons(elem, "description") or _xml_text_nons(elem, "summary")
    skills = _extract_skills_from_text(description or title)

    # Extract date
    posted = _xml_text_nons(elem, "pubDate") or _xml_text_nons(elem, "published") or _xml_text_nons(elem, "updated")

    return ATSJob(
        source="career_rss",
        url=link or "",
        title=title,
        description=description,
        skills=skills,
        posted_date=posted,
    )


# ---------------------------------------------------------------------------
# Strategy 3: Link-based extraction (fallback)
# ---------------------------------------------------------------------------

# CSS selectors for job-related links on Austrian career pages.
# Ordered by specificity: German paths first, then English.
_JOB_LINK_SELECTORS = (
    "a[href*='/job/']",
    "a[href*='/jobs/']",
    "a[href*='/stellenangebote/']",
    "a[href*='/stellenanzeige/']",
    "a[href*='/karriere/']",
    "a[href*='/position/']",
    "a[href*='/open-positions/']",
    "a[href*='/careers/opening']",
    "a[href*='/bewerbung/']",
)

# Title-like containers near job links
_TITLE_SELECTORS = "h2, h3, h4, .job-title, .position-title, .stellenanzeige-title"


def extract_link_based_jobs(html: str | bytes, base_url: str, company: str = "") -> list[ATSJob]:
    """Extract jobs by scanning for job-related <a> tags.

    This is the fallback when JSON-LD and RSS are unavailable. It finds
    links whose href contains job-related path segments, then attempts to
    extract the title from the link text or a nearby heading.
    """
    soup = BeautifulSoup(html, "lxml")

    # Combine all selectors into one query
    all_links: list[Any] = []
    for selector in _JOB_LINK_SELECTORS:
        all_links.extend(soup.select(selector))

    # Deduplicate by href
    seen_hrefs: set[str] = set()
    jobs: list[ATSJob] = []

    for link in all_links:
        href = link.get("href", "")
        if not href or "#" in href or "javascript:" in href:
            continue

        full_url = urljoin(base_url, href)
        if full_url in seen_hrefs:
            continue
        seen_hrefs.add(full_url)

        # Extract title from link text
        title = link.get_text(strip=True)
        if not title or len(title) < 5 or len(title) > 200:
            # Try parent or sibling
            parent = link.find_parent()
            if parent:
                title_elem = parent.select_one(_TITLE_SELECTORS)
                if title_elem:
                    title = title_elem.get_text(strip=True)

        if not title or len(title) < 5:
            continue

        # Skip navigation/utility links
        _NAVIGATION_SKIP = {
            "alle jobs", "all jobs", "mehr", "more", "weiter", "continue",
            "bewerben", "apply", "zurück", "back", "filter", "sortieren",
            "anzeigen", "show", "suchen", "search", "seite", "page",
            "nächste", "next", "vorherige", "previous",
        }
        title_lower = title.lower().strip()
        if title_lower in _NAVIGATION_SKIP:
            continue

        # Skip bare navigation links with no meaningful job title
        _NAVIGATION_TEXT_PATTERNS = (
            r"^alle anzeigen$", r"^more jobs?$", r"^show jobs?$", r"^job \d+$",
            r"^position \d+$", r"^open position$", r"^view job$", r"^details$",
            r"^job listing$", r"^job postings$", r"^vacancies$",
        )
        if any(re.match(pattern, title_lower, re.IGNORECASE) for pattern in _NAVIGATION_TEXT_PATTERNS):
            continue

        jobs.append(ATSJob(
            source="career_link",
            url=full_url,
            title=title,
            company=company or None,
        ))

    return jobs


# ---------------------------------------------------------------------------
# Master extraction function (dispatch)
# ---------------------------------------------------------------------------


def extract_career_page_jobs(
    html: str | bytes,
    base_url: str,
    company: str = "",
) -> list[ATSJob]:
    """Extract all jobs from a career page, trying multiple strategies.

    Strategy order (first non-empty result wins):
      1. JSON-LD JobPosting blocks (structured, most reliable)
      2. RSS/Atom XML feed (if the page IS a feed)
      3. Link-based extraction (fallback, noisy)

    Returns a deduplicated list of ATSJob objects.
    """
    # Strategy 1: JSON-LD
    jobs = extract_json_ld_jobs(html)
    if jobs:
        logger.debug("career_page_extractor: JSON-LD found %d jobs for %s", len(jobs), company)
        return jobs

    # Strategy 2: RSS (only if the content looks like XML)
    if isinstance(html, bytes):
        peek = html[:200].decode("utf-8", errors="replace").strip()
    else:
        peek = html[:200].strip()

    if peek.startswith("<?xml") or "<rss" in peek or "<feed" in peek:
        rss_jobs = extract_rss_feed(html)
        if rss_jobs:
            logger.debug("career_page_extractor: RSS found %d jobs for %s", len(rss_jobs), company)
            return rss_jobs

    # Strategy 3: Link-based fallback
    link_jobs = extract_link_based_jobs(html, base_url, company)
    if link_jobs:
        logger.debug("career_page_extractor: link-based found %d jobs for %s", len(link_jobs), company)
    return link_jobs


# ---------------------------------------------------------------------------
# Sitemap job discovery
# ---------------------------------------------------------------------------


def extract_jobs_from_sitemap(xml_text: str | bytes, base_url: str = "") -> list[str]:
    """Extract job URLs from a sitemap.xml.

    Many companies expose ``/sitemap.xml`` or ``/karriere/sitemap.xml``
    with job detail page URLs. This function returns a list of URLs that
    look like job detail pages (containing ``/job/``, ``/stellen/``, etc.).

    The pipeline can then fetch each URL individually for JSON-LD extraction.
    """
    if isinstance(xml_text, bytes):
        xml_text = xml_text.decode("utf-8", errors="replace")

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # Standard sitemap namespace
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    urls: list[str] = []
    # Try with namespace
    for url_elem in root.findall(".//sm:url", ns):
        loc = url_elem.find("sm:loc", ns)
        if loc is not None and loc.text:
            urls.append(loc.text.strip())

    # Fallback: no namespace
    if not urls:
        for url_elem in root.findall(".//{*}url"):
            loc = url_elem.find("{*}loc")
            if loc is not None and loc.text:
                urls.append(loc.text.strip())

    # Filter to only job-related URLs
    job_patterns = re.compile(
        r"/job/|/jobs/|/stellen|/position/|/careers/|/karriere/|/bewerbung/",
        re.I,
    )
    job_urls = [u for u in urls if job_patterns.search(u)]

    if base_url and not job_urls:
        # If no job-pattern URLs found, return all URLs (maybe the sitemap
        # is already filtered to jobs only)
        return urls

    return job_urls
