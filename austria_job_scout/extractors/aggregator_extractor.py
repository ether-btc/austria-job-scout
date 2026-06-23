"""Aggregator extractors: karriere.at, jobs.at, willhaben.at."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class AggregatorJob:
    """Parsed job from an aggregator listing."""

    source: str  # karriere_at, jobs_at, willhaben_at
    url: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    snippet: str | None = None
    posted_date: str | None = None
    salary: str | None = None
    employment_type: str | None = None
    remote: bool = False


# ---------------------------------------------------------------------------
# karriere.at extractor
# ---------------------------------------------------------------------------


def extract_karriere_at_listing(html: str | bytes, base_url: str) -> list[AggregatorJob]:
    """Extract jobs from a karriere.at search results page."""
    jobs = []
    soup = BeautifulSoup(html, "lxml")

    # karriere.at job cards are typically in <article> or <li class="job-item">
    # This is a heuristic based on common patterns
    job_elements = soup.select("article.job-item, li.job-item, div.job-card, .job-listing-item")

    for elem in job_elements:
        # Extract URL
        link = elem.select_one("a.job-link, a[data-js-element='job-link'], a[href*='/jobs/']")
        if not link:
            continue

        url = urljoin(base_url, link.get("href", ""))
        if not url or "#" in url:
            continue

        # Extract title
        title = elem.select_one("h2, h3, .job-title, [data-js-element='job-title']")
        title_text = title.get_text(strip=True) if title else None

        # Extract company
        company = elem.select_one(".company-name, .job-company, [data-js-element='company']")
        company_text = company.get_text(strip=True) if company else None

        # Extract location
        location = elem.select_one(".job-location, .location, [data-js-element='location']")
        location_text = location.get_text(strip=True) if location else None

        # Extract snippet
        snippet = elem.select_one(".job-snippet, .job-description-short, p")
        snippet_text = snippet.get_text(strip=True) if snippet else None

        # Extract date
        date_elem = elem.select_one(".date, .posted-date, time")
        date_text = date_elem.get_text(strip=True) if date_elem else None

        jobs.append(
            AggregatorJob(
                source="karriere_at",
                url=url,
                title=title_text,
                company=company_text,
                location=location_text,
                snippet=snippet_text,
                posted_date=date_text,
            )
        )

    return jobs


# ---------------------------------------------------------------------------
# jobs.at extractor
# ---------------------------------------------------------------------------


def extract_jobs_at_listing(html: str | bytes, base_url: str) -> list[AggregatorJob]:
    """Extract jobs from a jobs.at search results page."""
    jobs = []
    soup = BeautifulSoup(html, "lxml")

    # jobs.at uses similar patterns to karriere.at
    job_elements = soup.select("article.job, .job-entry, .job-list-item, [data-job-id]")

    for elem in job_elements:
        # Extract URL
        link = elem.select_one("a.job-title-link, a.job-link, a[href*='/job/']")
        if not link:
            continue

        url = urljoin(base_url, link.get("href", ""))
        if not url or "#" in url:
            continue

        # Extract title
        title = elem.select_one(".job-title, h3, h2")
        title_text = title.get_text(strip=True) if title else None

        # Extract company
        company = elem.select_one(".company, .company-name, .employer")
        company_text = company.get_text(strip=True) if company else None

        # Extract location
        location = elem.select_one(".location, .city, .job-location")
        location_text = location.get_text(strip=True) if location else None

        # Extract snippet
        snippet = elem.select_one(".snippet, .description-short, .job-desc-short")
        snippet_text = snippet.get_text(strip=True) if snippet else None

        jobs.append(
            AggregatorJob(
                source="jobs_at",
                url=url,
                title=title_text,
                company=company_text,
                location=location_text,
                snippet=snippet_text,
            )
        )

    return jobs


# ---------------------------------------------------------------------------
# willhaben.at extractor (careers section)
# ---------------------------------------------------------------------------


def extract_willhaben_at_listing(html: str | bytes, base_url: str) -> list[AggregatorJob]:
    """Extract jobs from a willhaben.at careers page."""
    jobs = []
    soup = BeautifulSoup(html, "lxml")

    # willhaben.at uses specific class names for job listings
    job_elements = soup.select(".css-job-list-item, .job-offer, [data-testid='job-item']")

    for elem in job_elements:
        # Extract URL
        link = elem.select_one("a[href*='/job/']")
        if not link:
            continue

        url = urljoin(base_url, link.get("href", ""))
        if not url or "#" in url:
            continue

        # Extract title
        title = elem.select_one(".job-title, h2, h3")
        title_text = title.get_text(strip=True) if title else None

        # Extract company
        company = elem.select_one(".company, .employer-name")
        company_text = company.get_text(strip=True) if company else None

        # Extract location
        location = elem.select_one(".location, .city, .address")
        location_text = location.get_text(strip=True) if location else None

        # Extract salary
        salary = elem.select_one(".salary, .payment, .salary-range")
        salary_text = salary.get_text(strip=True) if salary else None

        jobs.append(
            AggregatorJob(
                source="willhaben_at",
                url=url,
                title=title_text,
                company=company_text,
                location=location_text,
                salary=salary_text,
            )
        )

    return jobs


# ---------------------------------------------------------------------------
# Career path extractor (direct company career pages)
# ---------------------------------------------------------------------------


def extract_career_page(html: str | bytes, base_url: str, company: str) -> list[AggregatorJob]:
    """Extract job links from a generic company career page."""
    jobs = []
    soup = BeautifulSoup(html, "lxml")

    # Look for job-related links
    # Common patterns: /jobs/, /careers/, /openings/, /positions/
    job_links = soup.select(
        "a[href*='/job/'], a[href*='/jobs/'], a[href*='/careers/openings/'], "
        "a[href*='/positions/'], a[href*='/open-positions/']"
    )

    seen_urls = set()
    for link in job_links:
        url = urljoin(base_url, link.get("href", ""))

        # Deduplicate and filter
        if url in seen_urls or "#" in url:
            continue
        seen_urls.add(url)

        # Extract title from link text or nearby heading
        title = link.get_text(strip=True)
        if not title or len(title) > 100:  # Skip empty or suspiciously long titles
            # Try to find title from nearby element
            parent = link.parent
            if parent:
                title_elem = parent.select_one("h2, h3, .job-title, .position-title")
                title = title_elem.get_text(strip=True) if title_elem else None

        if not title:
            continue

        jobs.append(AggregatorJob(source="career_page", url=url, title=title, company=company))

    return jobs


# ---------------------------------------------------------------------------
# Universal extractor (dispatch)
# ---------------------------------------------------------------------------


def extract_from_url(url: str) -> list[AggregatorJob] | None:
    """Fetch a URL and extract aggregator job listings."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        return None

    return extract_from_html(url, response.text)


def extract_from_html(url: str, html: str) -> list[AggregatorJob] | None:
    """Extract aggregator job listings from pre-fetched HTML (avoids double-fetch).

    Pillar 0-compliant: pipeline already fetched the page, so we parse
    the HTML directly instead of making a second HTTP request.
    """
    # Detect source from URL patterns
    if "karriere.at" in url:
        return extract_karriere_at_listing(html, url)
    elif "jobs.at" in url:
        return extract_jobs_at_listing(html, url)
    elif "willhaben.at" in url:
        return extract_willhaben_at_listing(html, url)
    else:
        # Generic career page extraction
        company = _guess_company_from_url(url)
        return extract_career_page(html, url, company)


def _guess_company_from_url(url: str) -> str:
    """Guess company name from URL (e.g., https://acme.com/careers -> acme)."""
    # Extract domain
    match = re.search(r"https?://(?:www\.)?([^/]+)", url)
    if not match:
        return "Unknown"

    domain = match.group(1)
    # Remove TLD
    company = domain.rsplit(".", 1)[0]
    # Clean up common subdomains
    for prefix in ["jobs.", "careers.", "career."]:
        if company.startswith(prefix):
            company = company[len(prefix) :]
            break

    return company.title().replace("-", " ")