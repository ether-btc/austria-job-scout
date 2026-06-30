"""ATS extractors: JSON + XML + JSON-LD parsing for ATS job boards.

Handles two response types:
  - JSON API feeds (Greenhouse, Lever, SmartRecruiters, Personio XML)
  - HTML pages with embedded JSON-LD (Workday, Greenhouse career pages)

The dispatch logic in extract_from_html() auto-detects: if the body parses
as JSON or XML, it goes through the structured parsers. If it's HTML, it
falls back to JSON-LD + script-var extraction.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ATSJob:
    """Parsed job posting from an ATS."""

    source: str
    url: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None
    seniority: str | None = None
    employment_type: str | None = None  # full-time, part-time, contract
    remote: bool = False
    skills: list[str] = field(default_factory=list)
    posted_date: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str = "EUR"
    raw_json: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# JSON-LD extractor (universal for most modern ATS)
# ---------------------------------------------------------------------------


def extract_json_ld(html: str | bytes) -> list[dict[str, Any]]:
    """Extract all JSON-LD blocks from HTML."""
    soup = BeautifulSoup(html, "lxml")
    scripts = soup.find_all("script", type="application/ld+json")
    results = []

    for script in scripts:
        try:
            data = json.loads(script.string or "{}")
            if isinstance(data, list):
                results.extend(data)
            elif isinstance(data, dict):
                results.append(data)
        except json.JSONDecodeError:
            continue

    return results


def parse_json_ld_job(job_data: dict[str, Any]) -> ATSJob | None:
    """Parse a JobPosting JSON-LD object."""
    job_type = job_data.get("@type")
    if job_type != "JobPosting":
        return None

    # Extract salary
    salary = job_data.get("baseSalary") or {}
    if isinstance(salary, dict):
        salary_min = salary.get("value", {}).get("minValue")
        salary_max = salary.get("value", {}).get("maxValue")
        currency = salary.get("value", {}).get("currency", "EUR")
    else:
        salary_min = salary_max = currency = None

    # Extract skills from description
    description = job_data.get("description") or ""
    skills = _extract_skills_from_text(description)

    return ATSJob(
        source="json_ld",
        url=job_data.get("url") or "",
        title=job_data.get("title"),
        company=job_data.get("hiringOrganization", {}).get("name"),
        location=job_data.get("jobLocation", {}).get("address", {}).get("addressLocality"),
        description=description,
        seniority=job_data.get("experienceRequirements"),
        employment_type=job_data.get("employmentType"),
        remote=_is_remote(job_data),
        skills=skills,
        posted_date=job_data.get("datePosted"),
        salary_min=int(salary_min) if salary_min and isinstance(salary_min, (int, float)) else None,
        salary_max=int(salary_max) if salary_max and isinstance(salary_max, (int, float)) else None,
        currency=currency or "EUR",
        raw_json=job_data,
    )


# ---------------------------------------------------------------------------
# Workday-specific extractor
# ---------------------------------------------------------------------------


def extract_workday(html: str | bytes) -> list[ATSJob]:
    """Extract jobs from Workday pages (structured JSON embedded in script)."""
    jobs = []

    # Workday often exposes data in a __WORKDAY__ script variable
    soup = BeautifulSoup(html, "lxml")

    # Try to find embedded job data
    script_vars = soup.find_all("script")
    for script in script_vars:
        script_text = script.string or ""
        if "__WORKDAY__" in script_text or "jobPosting" in script_text:
            # Try to extract JSON-like structures
            json_matches = re.findall(r'\{[^{}]*(?:"title"[^{}]*"[^{}]*\})+[^{}]*\}', script_text)
            for match in json_matches:
                try:
                    data = json.loads(match)
                    if "title" in data:
                        job = _parse_workday_job(data)
                        if job:
                            jobs.append(job)
                except json.JSONDecodeError:
                    continue

    # Fallback to JSON-LD
    json_ld = extract_json_ld(html)
    for item in json_ld:
        job = parse_json_ld_job(item)
        if job and "workday" in job.raw_json.get("url", "").lower():
            job.source = "workday"
            jobs.append(job)

    return jobs


def _parse_workday_job(data: dict[str, Any]) -> ATSJob | None:
    """Parse a Workday job data dict."""
    return ATSJob(
        source="workday",
        url=data.get("externalUrl") or "",
        title=data.get("title"),
        company=data.get("companyName"),
        location=data.get("location"),
        description=data.get("description"),
        skills=_extract_skills_from_text(data.get("description") or ""),
        raw_json=data,
    )


def _try_parse_json(text: str | bytes) -> dict | list | None:
    """Try to parse text as JSON. Returns None if not valid JSON."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
        if isinstance(data, (dict, list)):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _try_parse_xml(text: str | bytes) -> ET.Element | None:
    """Try to parse text as XML. Returns None if not valid XML."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


# ---------------------------------------------------------------------------
# Greenhouse-specific extractor (JSON API + HTML fallback)
# ---------------------------------------------------------------------------


def extract_greenhouse(html_or_json: str | bytes) -> list[ATSJob]:
    """Extract jobs from Greenhouse.

    Handles two response types:
      - JSON from boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
        → {"jobs": [{"id":..., "title":..., "absolute_url":..., "content":...}]}
      - HTML career page with embedded JSON-LD
    """
    # Try JSON first — the API endpoint returns pure JSON
    data = _try_parse_json(html_or_json)
    if data is not None:
        jobs_list = data if isinstance(data, list) else data.get("jobs", [])
        parsed = [_parse_greenhouse_json_job(j) for j in jobs_list]
        return [j for j in parsed if j is not None]

    # Fallback to HTML + JSON-LD
    jobs: list[ATSJob] = []
    soup = BeautifulSoup(html_or_json, "lxml")

    for script in soup.find_all("script"):
        script_text = script.string or ""
        if "greenhouseData" in script_text or "Greenhouse" in script_text:
            json_matches = re.findall(r'\{[^{}]*(?:"title"[^{}]*"[^{}]*\})+[^{}]*\}', script_text)
            for match in json_matches:
                try:
                    d = json.loads(match)
                    if "title" in d:
                        job = _parse_greenhouse_job(d)
                        if job:
                            jobs.append(job)
                except json.JSONDecodeError:
                    continue

    json_ld = extract_json_ld(html_or_json)
    for item in json_ld:
        job = parse_json_ld_job(item)
        if job and "greenhouse" in job.raw_json.get("url", "").lower():
            job.source = "greenhouse"
            jobs.append(job)

    return jobs


def _parse_greenhouse_json_job(data: dict[str, Any]) -> ATSJob | None:
    """Parse a job dict from Greenhouse JSON API.

    Key fields (from boards-api.greenhouse.io):
      - id, title, updated_at, absolute_url, content (HTML),
        location{name}, departments[], offices[],
        employment (may be None), metadata[]
    """
    if not data.get("title"):
        return None
    return ATSJob(
        source="greenhouse",
        url=data.get("absolute_url") or "",
        title=data.get("title"),
        company=None,  # Greenhouse API doesn't include company name
        location=data.get("location", {}).get("name") if isinstance(data.get("location"), dict) else None,
        description=data.get("content"),
        employment_type=data.get("employment"),
        skills=_extract_skills_from_text(data.get("content") or ""),
        posted_date=data.get("updated_at"),
        raw_json=data,
    )


def _parse_greenhouse_job(data: dict[str, Any]) -> ATSJob | None:
    """Parse a Greenhouse job from embedded HTML script data (legacy)."""
    return ATSJob(
        source="greenhouse",
        url=data.get("absolute_url") or "",
        title=data.get("title"),
        company=data.get("company", {}).get("name"),
        location=data.get("location", {}).get("name"),
        description=data.get("content"),
        employment_type=data.get("employment"),
        skills=_extract_skills_from_text(data.get("content") or ""),
        raw_json=data,
    )


# ---------------------------------------------------------------------------
# Lever-specific extractor
# ---------------------------------------------------------------------------


def extract_lever(html_or_json: str | bytes) -> list[ATSJob]:
    """Extract jobs from Lever.

    Handles two response types:
      - JSON from api.lever.co/v0/postings/{token}?mode=json
        → [{"id":..., "text":..., "hostedUrl":..., "descriptionPlain":...}]
      - HTML career page with embedded JSON-LD
    """
    # Try JSON first
    data = _try_parse_json(html_or_json)
    if data is not None:
        postings = data if isinstance(data, list) else data.get("postings", data.get("data", []))
        parsed = [_parse_lever_json_job(j) for j in postings]
        return [j for j in parsed if j is not None]

    # Fallback to HTML + JSON-LD
    jobs: list[ATSJob] = []
    soup = BeautifulSoup(html_or_json, "lxml")

    for script in soup.find_all("script"):
        script_text = script.string or ""
        if "leverData" in script_text or "lever.co" in script_text:
            # Extract job listings
            json_matches = re.findall(r'\{[^{}]*(?:"title"[^{}]*"[^{}]*\})+[^{}]*\}', script_text)
            for match in json_matches:
                try:
                    data = json.loads(match)
                    if "title" in data:
                        job = _parse_lever_job(data)
                        if job:
                            jobs.append(job)
                except json.JSONDecodeError:
                    continue

    # Fallback to JSON-LD
    json_ld = extract_json_ld(html_or_json)
    for item in json_ld:
        job = parse_json_ld_job(item)
        if job and "lever.co" in job.raw_json.get("url", "").lower():
            job.source = "lever"
            jobs.append(job)

    return jobs


def _parse_lever_json_job(data: dict[str, Any]) -> ATSJob | None:
    """Parse a posting from Lever JSON API.

    Key fields (from api.lever.co/v0/postings/{token}?mode=json):
      - id, text (title), hostedUrl, description (HTML),
        descriptionPlain (text), applyUrl,
        categories{commitment, location, team, allLocations},
        createdAt
    """
    if not data.get("text"):
        return None
    return ATSJob(
        source="lever",
        url=data.get("hostedUrl") or "",
        title=data.get("text"),
        company=None,
        location=data.get("categories", {}).get("location") if isinstance(data.get("categories"), dict) else None,
        description=data.get("descriptionPlain") or data.get("description"),
        employment_type=data.get("categories", {}).get("commitment") if isinstance(data.get("categories"), dict) else None,
        skills=_extract_skills_from_text(data.get("descriptionPlain") or data.get("description") or ""),
        posted_date=data.get("createdAt"),
        raw_json=data,
    )


def _parse_lever_job(data: dict[str, Any]) -> ATSJob | None:
    """Parse a Lever job from embedded HTML script data (legacy)."""
    return ATSJob(
        source="lever",
        url=data.get("hostedUrl") or "",
        title=data.get("title"),
        company=data.get("company"),
        location=data.get("categories", {}).get("location"),
        description=data.get("description"),
        employment_type=data.get("categories", {}).get("commitment"),
        skills=_extract_skills_from_text(data.get("description") or ""),
        raw_json=data,
    )


# ---------------------------------------------------------------------------
# SmartRecruiters-specific extractor
# ---------------------------------------------------------------------------


def extract_smartrecruiters(html_or_json: str | bytes) -> list[ATSJob]:
    """Extract jobs from SmartRecruiters.

    Handles two response types:
      - JSON from api.smartrecruiters.com/v1/companies/{token}/postings
        → {"content": [{"id":..., "title":..., "location":{...}, ...}]}
      - HTML career page with JSON-LD
    """
    # Try JSON first
    data = _try_parse_json(html_or_json)
    if data is not None:
        content = data.get("content", []) if isinstance(data, dict) else data
        parsed = [_parse_smartrecruiters_json_job(j) for j in content]
        return [j for j in parsed if j is not None]

    # Fallback to JSON-LD
    jobs: list[ATSJob] = []
    json_ld = extract_json_ld(html_or_json)
    for item in json_ld:
        job = parse_json_ld_job(item)
        if job and "smartrecruiters" in job.raw_json.get("url", "").lower():
            job.source = "smartrecruiters"
            jobs.append(job)

    return jobs


def _parse_smartrecruiters_json_job(data: dict[str, Any]) -> ATSJob | None:
    """Parse a posting from SmartRecruiters JSON API."""
    if not data.get("title"):
        return None
    # SmartRecruiters location structure: {"city":..., "country":..., "region":...}
    loc = data.get("location", {})
    location_str = None
    if isinstance(loc, dict):
        parts = [str(loc.get(k)) for k in ("city", "region", "country") if loc.get(k)]
        location_str = ", ".join(parts) if parts else None

    # Apply URL construction
    job_id = data.get("id", "")
    company_id = data.get("company", {}).get("identifier", "") if isinstance(data.get("company"), dict) else ""
    apply_url = f"https://jobs.smartrecruiters.com/{company_id}/{job_id}" if company_id and job_id else ""

    return ATSJob(
        source="smartrecruiters",
        url=data.get("applyUrl") or apply_url,
        title=data.get("title"),
        company=data.get("company", {}).get("name") if isinstance(data.get("company"), dict) else None,
        location=location_str,
        description=data.get("jobAd", {}).get("sections", {}).get("jobDescription", {}).get("text")
            if isinstance(data.get("jobAd"), dict) else None,
        employment_type=data.get("typeOfEmployment", {}).get("label") if isinstance(data.get("typeOfEmployment"), dict) else None,
        skills=_extract_skills_from_text(
            (data.get("jobAd", {}).get("sections", {}).get("jobDescription", {}).get("text") or "")
            if isinstance(data.get("jobAd"), dict) else ""
        ),
        posted_date=data.get("releasedDate"),
        raw_json=data,
    )


# ---------------------------------------------------------------------------
# Personio XML extractor
# ---------------------------------------------------------------------------


def extract_personio(xml_or_html: str | bytes) -> list[ATSJob]:
    """Extract jobs from Personio XML feed.

    Personio publishes a public XML feed at ``{slug}.jobs.personio.de/xml``
    with root element ``<workzag-jobs>`` containing ``<position>`` children.

    Each ``<position>`` includes:
      - ``<id>``, ``<name>`` (job title), ``<office>`` (location),
        ``<jobDescriptions><jobDescription>`` (sections with name+value),
        ``<employmentType>``, ``<schedule>``, ``<department>``,
        ``<createdAt>``, ``<updatedAt>``
    """
    # Try XML first
    root = _try_parse_xml(xml_or_html)
    if root is not None:
        return _parse_personio_xml(root)

    # Fallback: Personio also has an HTML career page with JSON-LD
    jobs: list[ATSJob] = []
    json_ld = extract_json_ld(xml_or_html)
    for item in json_ld:
        job = parse_json_ld_job(item)
        if job and "personio" in job.raw_json.get("url", "").lower():
            job.source = "personio"
            jobs.append(job)
    return jobs


def _parse_personio_xml(root: ET.Element) -> list[ATSJob]:
    """Parse the <workzag-jobs> XML root into ATSJob objects."""
    # Strip namespace if present
    tag = root.tag
    if "}" in tag:
        tag = tag.split("}", 1)[1]

    positions: list[ET.Element] = []
    if tag == "workzag-jobs":
        positions = list(root)
    else:
        # Maybe root IS a single position, or we need to search
        positions = root.findall(".//position") or list(root)

    jobs: list[ATSJob] = []
    for pos in positions:
        ptag = pos.tag.split("}", 1)[-1] if "}" in pos.tag else pos.tag
        if ptag != "position":
            continue
        job = _parse_personio_position(pos)
        if job:
            jobs.append(job)
    return jobs


def _xml_text(elem: ET.Element | None, tag: str) -> str | None:
    """Get text of the first child with the given tag (namespace-agnostic)."""
    if elem is None:
        return None
    # Try direct find, then namespace-stripping find
    child = elem.find(tag)
    if child is None:
        child = elem.find(f".//{{*}}{tag}")
    if child is not None and child.text:
        return child.text.strip()
    return None


def _parse_personio_position(pos: ET.Element) -> ATSJob | None:
    """Parse one <position> element from the Personio XML feed."""
    name = _xml_text(pos, "name")
    if not name:
        return None

    pos_id = _xml_text(pos, "id") or ""
    office = _xml_text(pos, "office") or ""
    department = _xml_text(pos, "department")
    employment = _xml_text(pos, "employmentType") or _xml_text(pos, "schedule")

    # Build the job URL
    slug = ""
    # URL pattern: https://{slug}.jobs.personio.de/job/{id}
    url = f"https://personio.de/job/{pos_id}" if pos_id else ""

    # Extract description from <jobDescriptions>
    description_parts: list[str] = []
    job_descs = pos.find("jobDescriptions")
    if job_descs is not None:
        for jd in job_descs.findall("jobDescription"):
            section_name = _xml_text(jd, "name") or ""
            section_value = _xml_text(jd, "value") or ""
            if section_value:
                if section_name:
                    description_parts.append(f"## {section_name}\n\n{section_value}")
                else:
                    description_parts.append(section_value)

    description = "\n\n".join(description_parts) if description_parts else None
    skills = _extract_skills_from_text(description or name)

    # Build raw_json for compatibility with ATSJob dataclass
    raw: dict[str, Any] = {
        "id": pos_id,
        "name": name,
        "office": office,
        "department": department,
        "employmentType": employment,
    }

    return ATSJob(
        source="personio",
        url=url,
        title=name,
        company=None,  # Not in the XML; comes from the seed
        location=office or None,
        description=description,
        employment_type=employment,
        skills=skills,
        posted_date=_xml_text(pos, "createdAt"),
        raw_json=raw,
    )


# ---------------------------------------------------------------------------
# Universal extractor (dispatch)
# ---------------------------------------------------------------------------


def extract_from_url(url: str) -> ATSJob | None:
    """Fetch a URL and extract ATS job data (auto-detect ATS)."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        return None

    return extract_from_html(url, response.text)


def extract_from_html(url: str, html: str) -> ATSJob | None:
    """Extract ATS job data from pre-fetched HTML (avoids double-fetch).

    This is the Pillar 0-compliant entry point: the pipeline already
    fetched the page in Step 3, so we parse the HTML directly instead
    of making a second HTTP request.
    """
    # Detect ATS from URL patterns
    if "workday" in url.lower():
        jobs = extract_workday(html)
    elif "greenhouse" in url.lower():
        jobs = extract_greenhouse(html)
    elif "lever.co" in url.lower():
        jobs = extract_lever(html)
    elif "smartrecruiters" in url.lower():
        jobs = extract_smartrecruiters(html)
    elif "personio" in url.lower():
        jobs = extract_personio(html)
    else:
        # Generic JSON-LD fallback
        json_ld = extract_json_ld(html)
        jobs = [parse_json_ld_job(item) for item in json_ld]
        jobs = [j for j in jobs if j is not None]

    return jobs[0] if jobs else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_remote(job_data: dict[str, Any]) -> bool:
    """Detect if job is remote."""
    location = job_data.get("jobLocation", {})
    if isinstance(location, dict):
        address = location.get("address", {})
        if address.get("addressLocality", "").lower() == "remote":
            return True
        if address.get("addressRegion", "").lower() == "remote":
            return True

    # Check description for remote keywords
    description = job_data.get("description", "").lower()
    return any(kw in description for kw in ["remote", "home office", "wfh", "anywhere"])


def _extract_skills_from_text(text: str) -> list[str]:
    """Extract potential skills from job description."""
    # Simple heuristic: look for capitalized technical terms
    # Skill library integration is a future enhancement
    skills = []

    # Common technical terms
    tech_keywords = [
        "Python",
        "Rust",
        "Go",
        "Java",
        "JavaScript",
        "TypeScript",
        "React",
        "Angular",
        "Vue",
        "Node.js",
        "Docker",
        "Kubernetes",
        "AWS",
        "Azure",
        "GCP",
        "PostgreSQL",
        "MySQL",
        "MongoDB",
        "Redis",
        "Kafka",
        "GraphQL",
        "REST",
        "gRPC",
        "Linux",
        "Git",
        "CI/CD",
        "TDD",
        "Agile",
        "Scrum",
    ]

    text_lower = text.lower()
    for kw in tech_keywords:
        if kw.lower() in text_lower:
            skills.append(kw)

    # Extract years of experience patterns
    exp_patterns = re.findall(r"(\d+)\+?\s*years?\s*(?:of\s*)?(?:experience)?", text, re.IGNORECASE)
    if exp_patterns:
        years = [int(p) for p in exp_patterns if p.isdigit()]
        if years:
            skills.append(f"{max(years)}+ years experience")

    return list(set(skills))