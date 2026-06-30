"""Tests for JSON/XML API response parsing in ATS extractors.

Verifies that the extractors correctly handle:
  - Greenhouse JSON API responses (boards-api.greenhouse.io)
  - Lever JSON API responses (api.lever.co)
  - SmartRecruiters JSON API responses (api.smartrecruiters.com)
  - Personio XML API responses ({slug}.jobs.personio.de/xml)
"""
from __future__ import annotations

import json

import pytest

from austria_job_scout.extractors.ats_extractor import (
    ATSJob,
    extract_greenhouse,
    extract_lever,
    extract_personio,
    extract_smartrecruiters,
    extract_from_html,
)


# ---------------------------------------------------------------------------
# Greenhouse JSON
# ---------------------------------------------------------------------------

GREENHOUSE_JSON = json.dumps({
    "jobs": [
        {
            "id": 12345,
            "title": "Senior Software Engineer",
            "absolute_url": "https://boards.greenhouse.io/dynatrace/jobs/12345",
            "updated_at": "2024-06-01T10:00:00.000Z",
            "location": {"name": "Linz, Austria"},
            "content": "<p>We are looking for a Senior Rust Engineer with experience in Kubernetes and PostgreSQL.</p>",
            "employment": "Full-Time",
            "departments": [{"name": "Engineering"}],
            "metadata": [],
        },
        {
            "id": 67890,
            "title": "DevOps Engineer",
            "absolute_url": "https://boards.greenhouse.io/dynatrace/jobs/67890",
            "updated_at": "2024-06-02T10:00:00.000Z",
            "location": {"name": "Vienna, Austria"},
            "content": "<p>Docker, Kubernetes, Terraform, AWS.</p>",
        },
    ]
})


def test_greenhouse_json_parsed():
    """Greenhouse JSON API response extracts multiple jobs."""
    jobs = extract_greenhouse(GREENHOUSE_JSON)
    assert len(jobs) == 2
    assert jobs[0].source == "greenhouse"
    assert jobs[0].title == "Senior Software Engineer"
    assert "greenhouse.io" in jobs[0].url
    assert jobs[0].location == "Linz, Austria"


def test_greenhouse_json_skills_extracted():
    """Skills are extracted from the content field."""
    jobs = extract_greenhouse(GREENHOUSE_JSON)
    skills = jobs[0].skills
    assert "Rust" in skills
    assert "Kubernetes" in skills
    assert "PostgreSQL" in skills


def test_greenhouse_json_empty_jobs():
    """Empty jobs list returns empty."""
    jobs = extract_greenhouse(json.dumps({"jobs": []}))
    assert jobs == []


def test_greenhouse_json_missing_title_skipped():
    """Jobs without a title are skipped."""
    data = json.dumps({"jobs": [{"id": 1, "absolute_url": "https://x"}]})
    jobs = extract_greenhouse(data)
    assert len(jobs) == 0


# ---------------------------------------------------------------------------
# Lever JSON
# ---------------------------------------------------------------------------

LEVER_JSON = json.dumps([
    {
        "id": "abc123",
        "text": "Backend Engineer (Rust)",
        "hostedUrl": "https://jobs.lever.co/acme/abc123",
        "descriptionPlain": "We need a Rust engineer with PostgreSQL and Docker experience.",
        "description": "<p>We need a Rust engineer...</p>",
        "categories": {
            "commitment": "Full-Time",
            "location": "Vienna, AT",
            "team": "Engineering",
        },
        "createdAt": 1717000000,
    }
])


def test_lever_json_parsed():
    """Lever JSON API response extracts jobs."""
    jobs = extract_lever(LEVER_JSON)
    assert len(jobs) == 1
    assert jobs[0].source == "lever"
    assert jobs[0].title == "Backend Engineer (Rust)"
    assert "lever.co" in jobs[0].url
    assert jobs[0].location == "Vienna, AT"
    assert jobs[0].employment_type == "Full-Time"


def test_lever_json_skills_extracted():
    """Skills are extracted from descriptionPlain."""
    jobs = extract_lever(LEVER_JSON)
    assert "Rust" in jobs[0].skills
    assert "PostgreSQL" in jobs[0].skills


def test_lever_json_uses_description_fallback():
    """When descriptionPlain is missing, falls back to description."""
    data = json.dumps([{
        "id": "x1",
        "text": "Go Developer",
        "hostedUrl": "https://jobs.lever.co/x/x1",
        "description": "Go and Docker experience needed.",
        "categories": {"location": "Remote"},
    }])
    jobs = extract_lever(data)
    assert len(jobs) == 1
    assert "Go" in jobs[0].skills


# ---------------------------------------------------------------------------
# SmartRecruiters JSON
# ---------------------------------------------------------------------------

SMARTRECRUITERS_JSON = json.dumps({
    "content": [
        {
            "id": "abc-123",
            "title": "Data Scientist",
            "applyUrl": "https://jobs.smartrecruiters.com/acme/abc-123",
            "location": {"city": "Vienna", "country": "Austria", "region": "Wien"},
            "company": {"name": "ACME GmbH", "identifier": "acme"},
            "typeOfEmployment": {"label": "Full time"},
            "releasedDate": "2024-06-01T00:00:00.000Z",
            "jobAd": {
                "sections": {
                    "jobDescription": {
                        "text": "Python, TensorFlow, and Kubernetes experience required."
                    }
                }
            },
        }
    ]
})


def test_smartrecruiters_json_parsed():
    """SmartRecruiters JSON API response extracts jobs."""
    jobs = extract_smartrecruiters(SMARTRECRUITERS_JSON)
    assert len(jobs) == 1
    assert jobs[0].source == "smartrecruiters"
    assert jobs[0].title == "Data Scientist"
    assert "smartrecruiters.com" in jobs[0].url
    assert "Vienna" in (jobs[0].location or "")


def test_smartrecruiters_json_skills():
    """Skills are extracted from jobAd description."""
    jobs = extract_smartrecruiters(SMARTRECRUITERS_JSON)
    assert "Python" in jobs[0].skills
    assert "Kubernetes" in jobs[0].skills


# ---------------------------------------------------------------------------
# Personio XML
# ---------------------------------------------------------------------------

PERSONIO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>10001</id>
    <name>Senior Python Developer</name>
    <office>Vienna</office>
    <department>Engineering</department>
    <employmentType>Vollzeit</employmentType>
    <createdAt>2024-06-01</createdAt>
    <jobDescriptions>
      <jobDescription>
        <name>Ihre Aufgaben</name>
        <value>Wir suchen einen Python Entwickler mit Erfahrung in PostgreSQL, Docker und Kubernetes.</value>
      </jobDescription>
      <jobDescription>
        <name>Ihr Profil</name>
        <value>5+ Jahre Python Erfahrung, AWS, REST APIs.</value>
      </jobDescription>
    </jobDescriptions>
  </position>
  <position>
    <id>10002</id>
    <name>DevOps Engineer</name>
    <office>Linz</office>
    <department>Infrastructure</department>
    <schedule>Vollzeit</schedule>
    <createdAt>2024-06-02</createdAt>
    <jobDescriptions>
      <jobDescription>
        <name>Description</name>
        <value>Terraform, AWS, Linux und CI/CD.</value>
      </jobDescription>
    </jobDescriptions>
  </position>
</workzag-jobs>
"""


def test_personio_xml_parsed():
    """Personio XML feed extracts multiple positions."""
    jobs = extract_personio(PERSONIO_XML)
    assert len(jobs) == 2
    assert jobs[0].source == "personio"
    assert jobs[0].title == "Senior Python Developer"
    assert jobs[0].location == "Vienna"
    assert "personio" in jobs[0].url


def test_personio_xml_skills_extracted():
    """Skills are extracted from jobDescriptions."""
    jobs = extract_personio(PERSONIO_XML)
    assert "Python" in jobs[0].skills
    assert "PostgreSQL" in jobs[0].skills
    assert "Kubernetes" in jobs[0].skills


def test_personio_xml_description_sections():
    """Description combines jobDescription sections with headers."""
    jobs = extract_personio(PERSONIO_XML)
    desc = jobs[0].description or ""
    assert "Ihre Aufgaben" in desc
    assert "Ihr Profil" in desc
    assert "Python Entwickler" in desc


def test_personio_xml_employment_type():
    """Employment type is extracted from employmentType or schedule."""
    jobs = extract_personio(PERSONIO_XML)
    assert jobs[0].employment_type == "Vollzeit"
    assert jobs[1].employment_type == "Vollzeit"  # from <schedule>


def test_personio_xml_empty():
    """Empty XML returns empty list."""
    assert extract_personio("") == []
    assert extract_personio("not xml not json") == []


def test_personio_xml_missing_name_skipped():
    """Positions without a name are skipped."""
    xml = """<?xml version="1.0"?>
<workzag-jobs>
  <position><id>1</id></position>
</workzag-jobs>
"""
    jobs = extract_personio(xml)
    assert len(jobs) == 0


# ---------------------------------------------------------------------------
# Dispatch via extract_from_html
# ---------------------------------------------------------------------------

def test_dispatch_personio_url():
    """extract_from_html dispatches to Personio extractor for personio URLs."""
    jobs = extract_from_html("https://celum.jobs.personio.de/xml", PERSONIO_XML)
    # extract_from_html returns first job only
    assert jobs is not None
    assert jobs.source == "personio"
    assert jobs.title == "Senior Python Developer"
