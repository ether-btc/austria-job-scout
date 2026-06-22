"""Tests for the ATS classifier — pure function, no network."""
from __future__ import annotations

import pytest

from austria_job_scout.probes import ats_classifier


# ---------------------------------------------------------------------------
# Hostname-based classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://boards-api.greenhouse.io/v1/boards/acme/jobs", "greenhouse"),
    ("https://boards.greenhouse.io/acme", "greenhouse"),
    ("https://api.lever.co/v0/postings/acme?mode=json", "lever"),
    ("https://api.eu.lever.co/v0/postings/acme", "lever"),
    ("https://jobs.lever.co/acme", "lever"),
    ("https://api.smartrecruiters.com/v1/companies/acme/postings", "smartrecruiters"),
    ("https://jobs.smartrecruiters.com/acme", "smartrecruiters"),
    ("https://acme.myworkdayjobs.com/en-US/acme", "workday"),
    ("https://acme.jobs.personio.de/xml", "personio"),
    ("https://acme.jobs.personio.com/xml", "personio"),
    ("https://acme.successfactors.com/careers", "successfactors"),
    ("https://acme.workable.com/api/accounts/x", "workable"),
    ("https://acme.recruitee.com/api/offers/", "recruitee"),
    ("https://www.karriere.at/jobs", "karriere_at"),
    ("https://karriere.at/jobs/12345", "karriere_at"),
    ("https://www.stepstone.at/jobs", "stepstone_at"),
    ("https://www.jobs.at/?q=dev", "jobs_at"),
    ("https://at.indeed.com/jobs", "indeed_at"),
    ("https://www.indeed.at/jobs", "indeed_at"),
    ("https://www.willhaben.at/jobs", "willhaben"),
])
def test_classify_by_hostname(url, expected):
    assert ats_classifier.classify(url) == expected


# ---------------------------------------------------------------------------
# HTML-snippet fallback
# ---------------------------------------------------------------------------

def test_classify_by_html_greenhouse():
    html = '<script src="https://boards.greenhouse.io/widget"></script>'
    assert ats_classifier.classify("https://acme.com/careers", html) == "greenhouse"


def test_classify_by_html_workday():
    html = '<script>window.myworkdayjobs.com = ...</script>'
    assert ats_classifier.classify("https://acme.com/careers", html) == "workday"


def test_classify_by_html_personio():
    html = '<div data-personio-host="acme"></div>'
    assert ats_classifier.classify("https://acme.com/jobs", html) == "personio"


def test_classify_unknown_url():
    assert ats_classifier.classify("https://weird-site.example/") == "unknown"


def test_classify_empty_url():
    assert ats_classifier.classify("") == "unknown"


def test_classify_url_with_career_path_falls_to_generic_html():
    """A non-ATS URL that mentions /jobs → generic_html fallback."""
    assert ats_classifier.classify("https://random.example/jobs") == "generic_html"
    assert ats_classifier.classify("https://random.example/careers") == "generic_html"
    assert ats_classifier.classify("https://random.example/karriere") == "generic_html"


# ---------------------------------------------------------------------------
# API endpoint builder
# ---------------------------------------------------------------------------

def test_api_endpoint_greenhouse():
    assert ats_classifier.api_endpoint_for("greenhouse", "acme") == \
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"


def test_api_endpoint_lever():
    assert ats_classifier.api_endpoint_for("lever", "acme") == \
        "https://api.lever.co/v0/postings/acme?mode=json"


def test_api_endpoint_personio():
    assert ats_classifier.api_endpoint_for("personio", "acme") == \
        "https://acme.jobs.personio.de/xml"


def test_api_endpoint_workday_returns_none():
    """Workday has no public JSON API — must be HTML-scraped."""
    assert ats_classifier.api_endpoint_for("workday", "acme") is None


def test_api_endpoint_unknown_ats_returns_none():
    assert ats_classifier.api_endpoint_for("unknown", "acme") is None


def test_api_endpoint_empty_token_returns_none():
    assert ats_classifier.api_endpoint_for("greenhouse", "") is None
    assert ats_classifier.api_endpoint_for("greenhouse", None) is None


# ---------------------------------------------------------------------------
# is_html_scrape_only
# ---------------------------------------------------------------------------

def test_html_scrape_only_includes_waf_sites():
    for ats in ("workday", "successfactors", "karriere_at",
                "stepstone_at", "jobs_at", "indeed_at", "willhaben",
                "generic_html", "unknown"):
        assert ats_classifier.is_html_scrape_only(ats) is True


def test_html_scrape_only_excludes_json_xml_ats():
    for ats in ("greenhouse", "lever", "smartrecruiters", "personio",
                "workable", "recruitee"):
        assert ats_classifier.is_html_scrape_only(ats) is False
