"""Tests for content-level dedup: same job on multiple sources collapses to one."""
from __future__ import annotations

import json

import pytest

from austria_job_scout.extractors.ats_extractor import (
    ATSJob,
    content_hash,
    dedupe_jobs,
    extract_greenhouse,
    extract_lever,
)


def test_content_hash_same_title_same_company():
    """Same title+company → same hash regardless of source."""
    h1 = content_hash("Senior Rust Engineer", "ACME GmbH")
    h2 = content_hash("Senior Rust Engineer", "ACME GmbH")
    assert h1 == h2


def test_content_hash_normalises_whitespace():
    """Whitespace differences don't change the hash."""
    h1 = content_hash("Senior Rust Engineer", "ACME GmbH")
    h2 = content_hash("  Senior   Rust   Engineer  ", "  ACME  GmbH  ")
    assert h1 == h2


def test_content_hash_case_insensitive():
    """Case differences don't change the hash."""
    h1 = content_hash("Senior Rust Engineer", "ACME GmbH")
    h2 = content_hash("senior rust engineer", "acme gmbh")
    assert h1 == h2


def test_content_hash_different_company_different_hash():
    """Different company → different hash even with same title."""
    h1 = content_hash("Senior Rust Engineer", "ACME GmbH")
    h2 = content_hash("Senior Rust Engineer", "OtherCorp AG")
    assert h1 != h2


def test_content_hash_missing_fields():
    """Missing title/company still produces a stable hash."""
    h1 = content_hash(None, "ACME GmbH")
    h2 = content_hash("", "ACME GmbH")
    assert h1 == h2  # both treat missing as empty


def test_dedupe_jobs_collapses_same_content():
    """Two jobs with same (title, company) → only first kept."""
    job1 = ATSJob(source="greenhouse", url="https://a.com/1", title="Senior Engineer", company="ACME")
    job2 = ATSJob(source="lever", url="https://b.com/2", title="Senior Engineer", company="ACME")
    job3 = ATSJob(source="karriere_at", url="https://c.com/3", title="Junior Developer", company="ACME")

    result = dedupe_jobs([job1, job2, job3])
    assert len(result) == 2
    assert result[0] is job1  # first occurrence wins
    assert result[1] is job3


def test_dedupe_jobs_preserves_different_companies():
    """Same title at different companies → both kept."""
    job1 = ATSJob(source="greenhouse", url="https://a.com/1", title="Senior Engineer", company="ACME")
    job2 = ATSJob(source="lever", url="https://b.com/2", title="Senior Engineer", company="OtherCorp")
    result = dedupe_jobs([job1, job2])
    assert len(result) == 2


def test_dedupe_jobs_empty():
    """Empty list returns empty."""
    assert dedupe_jobs([]) == []


def test_dedupe_jobs_no_duplicates():
    """All-different list returns unchanged."""
    job1 = ATSJob(source="x", url="a", title="A", company="X")
    job2 = ATSJob(source="x", url="b", title="B", company="X")
    job3 = ATSJob(source="x", url="c", title="C", company="Y")
    result = dedupe_jobs([job1, job2, job3])
    assert len(result) == 3


def test_greenhouse_plus_lever_same_job_dedupes():
    """End-to-end: same job from Greenhouse and Lever → dedup."""
    # Same job on Greenhouse and Lever
    greenhouse_json = json.dumps({
        "jobs": [{
            "id": 1,
            "title": "Senior Rust Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/1",
            "content": "Rust, Kubernetes, PostgreSQL",
        }],
    })
    lever_json = json.dumps([{
        "id": "x1",
        "text": "Senior Rust Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/x1",
        "descriptionPlain": "Rust, Kubernetes, PostgreSQL",
        "categories": {"location": "Vienna"},
    }])

    g_jobs = extract_greenhouse(greenhouse_json)
    l_jobs = extract_lever(lever_json)

    # Same title, but different company (Greenhouse API omits company; Lever too)
    # Add company manually to simulate aggregator's enrichment
    for j in g_jobs:
        j.company = "ACME GmbH"
    for j in l_jobs:
        j.company = "ACME GmbH"

    all_jobs = g_jobs + l_jobs
    deduped = dedupe_jobs(all_jobs)
    assert len(deduped) == 1
    assert deduped[0].source == "greenhouse"  # first wins
