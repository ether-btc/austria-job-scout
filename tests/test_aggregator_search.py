"""Tests for aggregator URL builders — pure, no network."""
from __future__ import annotations

from austria_job_scout.modules.ingest import ReferenceJob
from austria_job_scout.probes import aggregator_search


def _ref(role="Senior Rust Engineer", location="Wien", lang="en", skills=None):
    return ReferenceJob(
        source="role_name",
        raw_text=role,
        title=role,
        role_query=role,
        location=location,
        language=lang,
        skills=skills or ["Rust"],
        language_queries={
            "de": role if lang == "de" else role,
            "en": role if lang != "de" else role,
        },
    )


# ---------------------------------------------------------------------------
# karriere.at
# ---------------------------------------------------------------------------

def test_karriere_at_search_urls_has_role_query():
    ref = _ref()
    urls = aggregator_search.karriere_at_search_urls(ref)
    assert len(urls) >= 2
    for u in urls:
        assert u["ats"] == "karriere_at"
        assert "karriere.at/jobs" in u["url"]
        assert "q=" in u["url"]
        assert u["source_kind"] == "aggregator_query"
        assert 0 < u["predicted_relevance"] <= 1.0


def test_karriere_at_search_urls_includes_location():
    ref = _ref(location="Graz")
    urls = aggregator_search.karriere_at_search_urls(ref)
    has_graz = any("Graz" in u["url"] for u in urls)
    assert has_graz, f"expected Graz in some URL: {urls}"


def test_karriere_at_search_urls_role_only_when_no_location():
    ref = _ref(location=None)
    urls = aggregator_search.karriere_at_search_urls(ref)
    # At least one URL has no location= param
    no_loc = [u for u in urls if "location=" not in u["url"]]
    assert len(no_loc) >= 1


def test_karriere_at_search_urls_empty_role_returns_role_only():
    ref = ReferenceJob(
        source="role_name", raw_text="",
        title="", role_query=None, language="unknown", skills=[]
    )
    urls = aggregator_search.karriere_at_search_urls(ref)
    # Even with no role_query, the role-only URL is still built from raw_text=""
    # — but since role is empty, no URLs at all.
    assert urls == []


def test_karriere_at_sitemap_url_is_static():
    assert aggregator_search.karriere_at_sitemap_url() == \
        "https://www.karriere.at/static/sitemaps/sitemap-jobs-https.xml"


# ---------------------------------------------------------------------------
# jobs.at
# ---------------------------------------------------------------------------

def test_jobs_at_search_urls_returns_one_url():
    ref = _ref()
    urls = aggregator_search.jobs_at_search_urls(ref)
    assert len(urls) == 1
    assert urls[0]["ats"] == "jobs_at"
    assert "jobs.at" in urls[0]["url"]


def test_jobs_at_urls_marked_as_waf_risk():
    ref = _ref()
    urls = aggregator_search.jobs_at_search_urls(ref)
    assert "WAF" in urls[0]["notes"] or "aggressive" in urls[0]["notes"].lower()


# ---------------------------------------------------------------------------
# AMS
# ---------------------------------------------------------------------------

def test_ams_search_urls_built():
    ref = _ref()
    urls = aggregator_search.ams_search_urls(ref)
    assert len(urls) == 1
    assert urls[0]["ats"] == "ams_ogd"
    assert "ams.at" in urls[0]["url"]


# ---------------------------------------------------------------------------
# all_aggregator_targets dispatch
# ---------------------------------------------------------------------------

def test_all_aggregator_targets_combines_and_sorts():
    ref = _ref()
    targets = aggregator_search.all_aggregator_targets(ref)
    assert len(targets) >= 3
    # Sorted by predicted_relevance DESC, then priority ASC
    for i in range(len(targets) - 1):
        a, b = targets[i], targets[i + 1]
        # Either relevance decreases, or equal and priority increases
        assert (a["predicted_relevance"] > b["predicted_relevance"]) or \
               (a["predicted_relevance"] == b["predicted_relevance"] and
                a["priority"] <= b["priority"])


def test_aggregator_urls_have_required_keys():
    ref = _ref()
    for t in aggregator_search.all_aggregator_targets(ref):
        for key in ("ats", "source_kind", "url", "predicted_relevance",
                    "priority", "language", "role_query", "notes"):
            assert key in t, f"missing {key!r} in {t}"
