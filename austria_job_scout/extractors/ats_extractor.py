"""ATS extractors: JSON+LD parsing for Workday, Greenhouse, Lever, SmartRecruiters."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import requests
from bs4 import BeautifulSoup

from austria_job_scout.modules.config import settings

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


# ---------------------------------------------------------------------------
# Greenhouse-specific extractor
# ---------------------------------------------------------------------------


def extract_greenhouse(html: str | bytes) -> list[ATSJob]:
    """Extract jobs from Greenhouse pages (structured JSON)."""
    jobs = []

    # Greenhouse uses a global greenhouseData object
    soup = BeautifulSoup(html, "lxml")

    for script in soup.find_all("script"):
        script_text = script.string or ""
        if "greenhouseData" in script_text or "Greenhouse" in script_text:
            # Extract job listings
            json_matches = re.findall(r'\{[^{}]*(?:"title"[^{}]*"[^{}]*\})+[^{}]*\}', script_text)
            for match in json_matches:
                try:
                    data = json.loads(match)
                    if "title" in data:
                        job = _parse_greenhouse_job(data)
                        if job:
                            jobs.append(job)
                except json.JSONDecodeError:
                    continue

    # Fallback to JSON-LD
    json_ld = extract_json_ld(html)
    for item in json_ld:
        job = parse_json_ld_job(item)
        if job and "greenhouse" in job.raw_json.get("url", "").lower():
            job.source = "greenhouse"
            jobs.append(job)

    return jobs


def _parse_greenhouse_job(data: dict[str, Any]) -> ATSJob | None:
    """Parse a Greenhouse job data dict."""
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


def extract_lever(html: str | bytes) -> list[ATSJob]:
    """Extract jobs from Lever pages (structured JSON)."""
    jobs = []

    soup = BeautifulSoup(html, "lxml")

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
    json_ld = extract_json_ld(html)
    for item in json_ld:
        job = parse_json_ld_job(item)
        if job and "lever.co" in job.raw_json.get("url", "").lower():
            job.source = "lever"
            jobs.append(job)

    return jobs


def _parse_lever_job(data: dict[str, Any]) -> ATSJob | None:
    """Parse a Lever job data dict."""
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


def extract_smartrecruiters(html: str | bytes) -> list[ATSJob]:
    """Extract jobs from SmartRecruiters pages (structured JSON)."""
    jobs = []

    # SmartRecruiters uses JSON-LD heavily
    json_ld = extract_json_ld(html)
    for item in json_ld:
        job = parse_json_ld_job(item)
        if job and "smartrecruiters" in job.raw_json.get("url", "").lower():
            job.source = "smartrecruiters"
            jobs.append(job)

    return jobs


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

    html = response.text

    # Detect ATS from URL patterns
    if "workday" in url.lower():
        jobs = extract_workday(html)
    elif "greenhouse" in url.lower():
        jobs = extract_greenhouse(html)
    elif "lever.co" in url.lower():
        jobs = extract_lever(html)
    elif "smartrecruiters" in url.lower():
        jobs = extract_smartrecruiters(html)
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
    # TODO: Could be improved with a skill library
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