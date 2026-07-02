"""Tests for RSS discovery module."""
from __future__ import annotations

import pytest

from austria_job_scout.modules.rss_discovery import (
    build_austrian_company_rss_urls,
    build_aggregator_rss_urls,
    build_wien_specific_rss_urls,
    extract_rss_jobs,
    get_rss_info,
    is_rss_feed,
    build_all_austrian_rss_targets,
)


# ---------------------------------------------------------------------------
# Test fixtures - RSS content
# ---------------------------------------------------------------------------

RSS_AUSTRIAN_COMPANY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
    <channel>
        <title>Tech Startup GmbH Karriere</title>
        <link>https://techstartup.at/karriere</link>
        <description>Offene Stellen bei Tech Startup GmbH</description>
        <language>de-AT</language>
        <lastBuildDate>Mon, 01 Jul 2024 09:00:00 +0000</lastBuildDate>
        <item>
            <title>Senior Software Engineer</title>
            <link>https://techstartup.at/jobs/senior-software-engineer</link>
            <description>Wir suchen erfahrene Entwickler mit Python-Kenntnissen...</description>
            <pubDate>Mon, 01 Jul 2024 08:00:00 +0000</pubDate>
            <category>Softwareentwicklung</category>
        </item>
        <item>
            <title>Frontend Developer</title>
            <link>https://techstartup.at/jobs/frontend-developer</link>
            <description>Erfahrung mit React und TypeScript...</description>
            <pubDate>Mon, 01 Jul 2024 07:00:00 +0000</pubDate>
            <category>Frontend</category>
        </item>
    </channel>
</rss>"""

RSS_ATOM_AUSTRIAN = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
    <title>Web Agency Wien Jobs</title>
    <link href="https://webagency.at/jobs" />
    <subtitle>Offene Stellen bei Web Agency Wien</subtitle>
    <updated>2024-07-01T09:00:00Z</updated>
    <author>
        <name>Web Agency Wien</name>
    </author>
    <entry>
        <title>Web Developer (m/w/d)</title>
        <link href="https://webagency.at/jobs/web-developer" />
        <summary>Wir suchen einen Web Developer für anspruchsvolle Projekte...</summary>
        <published>2024-07-01T08:00:00Z</published>
        <category term="Webentwicklung" />
    </entry>
</feed>"""

RSS_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
    <channel>
        <title>Empty Feed</title>
        <description>No jobs here</description>
    </channel>
</rss>"""

RSS_INVALID = """<invalid>xml content</not-valid>"""

HTML_WITH_RSS = """
<html>
<head>
    <title>Karriere bei Tech GmbH</title>
    <link rel="alternate" type="application/rss+xml" href="/karriere/feed" />
</head>
<body>
    <p>RSS feed available</p>
</body>
</html>
"""

HTML_NO_RSS = """
<html>
<head>
    <title>Karriere bei Tech GmbH</title>
</head>
<body>
    <p>Kein RSS Feed verfügbar</p>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_austrian_company_rss_urls():
    """Test RSS URL building for Austrian companies."""
    urls = build_austrian_company_rss_urls("techstartup.at")
    
    # Should have multiple URL patterns
    assert len(urls) > 5
    
    # Check for common patterns
    karriere_feed = [u for u in urls if "/karriere/feed" in u]
    assert len(karriere_feed) > 0
    
    jobs_feed = [u for u in urls if "/jobs/feed" in u]
    assert len(jobs_feed) > 0
    
    # Check for both with and without www
    urls_with_www = [u for u in urls if "www." in u]
    urls_without_www = [u for u in urls if "www." not in u]
    assert len(urls_with_www) > 0
    assert len(urls_without_www) > 0
    
    # Check all URLs use https
    for url in urls:
        assert url.startswith("https://")


def test_build_aggregator_rss_urls():
    """Test RSS URL building for job aggregators."""
    urls = build_aggregator_rss_urls()
    
    assert len(urls) > 5
    
    # Check for karriere.at
    karriere_urls = [u for u in urls if "karriere.at" in u]
    assert len(karriere_urls) > 0
    
    # Check for stepstone
    stepstone_urls = [u for u in urls if "stepstone" in u]
    assert len(stepstone_urls) > 0
    
    # Check for German aggregators with Austrian coverage
    german_urls = [u for u in urls if "stepstone.de" in u]
    assert len(german_urls) > 0


def test_build_wien_specific_rss_urls():
    """Test RSS URL building for Wien-specific sources."""
    urls = build_wien_specific_rss_urls()
    
    assert len(urls) > 3
    
    # Check for Wien-specific sources
    wien_urls = [u for u in urls if "wien.at" in u or "wirtschaftsagentur.at" in u]
    assert len(wien_urls) > 0
    
    # Check for university feeds
    university_urls = [u for u in urls if "univie.ac.at" in u or "tuwien.ac.at" in u]
    assert len(university_urls) > 0


def test_extract_rss_jobs_austrian_company():
    """Test extraction from Austrian company RSS feed."""
    jobs = extract_rss_jobs(RSS_AUSTRIAN_COMPANY)
    
    assert len(jobs) == 2
    
    # Check first job
    senior_job = jobs[0]
    assert senior_job.title == "Senior Software Engineer"
    assert senior_job.url == "https://techstartup.at/jobs/senior-software-engineer"
    assert "erfahrene Entwickler" in senior_job.description
    assert senior_job.posted_date == "Mon, 01 Jul 2024 08:00:00 +0000"
    assert senior_job.source == "rss_rss20"
    
    # Check second job
    frontend_job = jobs[1]
    assert frontend_job.title == "Frontend Developer"
    assert frontend_job.url == "https://techstartup.at/jobs/frontend-developer"
    assert "React und TypeScript" in frontend_job.description
    assert frontend_job.posted_date == "Mon, 01 Jul 2024 07:00:00 +0000"
    
    # Check that skills are extracted
    for job in jobs:
        assert len(job.skills) > 0
        assert job.company == "Tech Startup GmbH"


def test_extract_rss_jobs_atom():
    """Test extraction from Atom feed."""
    jobs = extract_rss_jobs(RSS_ATOM_AUSTRIAN)
    
    assert len(jobs) == 1
    
    job = jobs[0]
    assert job.title == "Web Developer (m/w/d)"
    assert job.url == "https://webagency.at/jobs/web-developer"
    assert "anspruchsvolle Projekte" in job.description
    assert job.posted_date == "2024-07-01T08:00:00Z"
    assert job.source == "rss_atom"
    assert job.company == "Web Agency Wien"


def test_extract_rss_jobs_empty():
    """Test extraction from empty RSS feed."""
    jobs = extract_rss_jobs(RSS_EMPTY)
    assert len(jobs) == 0


def test_extract_rss_jobs_invalid_xml():
    """Test extraction with invalid XML."""
    jobs = extract_rss_jobs(RSS_INVALID)
    assert len(jobs) == 0


def test_get_rss_info():
    """Test RSS feed metadata extraction."""
    info = get_rss_info(RSS_AUSTRIAN_COMPANY)
    
    assert info["type"] == "rss20"
    assert info["title"] == "Tech Startup GmbH Karriere"
    assert info["link"] == "https://techstartup.at/karriere"
    assert info["description"] == "Offene Stellen bei Tech Startup GmbH"
    assert info["language"] == "de-AT"
    assert info["last_build_date"] == "Mon, 01 Jul 2024 09:00:00 +0000"
    assert info["item_count"] == 2


def test_get_rss_info_atom():
    """Test Atom feed metadata extraction."""
    info = get_rss_info(RSS_ATOM_AUSTRIAN)
    
    assert info["type"] == "atom"
    assert info["title"] == "Web Agency Wien Jobs"
    assert info["link"] == "https://webagency.at/jobs"
    assert info["description"] == "Offene Stellen bei Web Agency Wien"
    assert info["language"] == ""
    assert info["last_build_date"] == "2024-07-01T09:00:00Z"
    assert info["item_count"] == 1


def test_get_rss_info_invalid():
    """Test RSS info extraction with invalid XML."""
    info = get_rss_info(RSS_INVALID)
    assert info == {}


def test_is_rss_feed():
    """Test RSS feed detection."""
    # Should detect RSS indicators
    assert is_rss_feed(RSS_AUSTRIAN_COMPANY) == True
    assert is_rss_feed(RSS_ATOM_AUSTRIAN) == True
    
    # Should detect RSS in HTML
    assert is_rss_feed(HTML_WITH_RSS) == True
    
    # Should not detect RSS in regular HTML
    assert is_rss_feed(HTML_NO_RSS) == False
    
    # Should not detect RSS in empty content
    assert is_rss_feed("") == False


def test_build_all_austrian_rss_targets():
    """Test building complete RSS target list."""
    seed_domains = ["techstartup.at", "webagency.at"]
    targets = build_all_austrian_rss_targets(seed_domains, include_aggregators=True)
    
    assert len(targets) > 10  # Should have company + aggregator feeds
    
    # Check company-specific targets
    company_targets = [t for t in targets if t["source_kind"] == "rss_company"]
    assert len(company_targets) == len(seed_domains) * len(build_austrian_company_rss_urls("test")) // 2  # approximate
    
    # Check aggregator targets  
    aggregator_targets = [t for t in targets if t["source_kind"] == "rss_aggregator"]
    assert len(aggregator_targets) > 5
    
    # Check target structure
    for target in targets:
        assert "ats" in target
        assert "source_kind" in target
        assert "url" in target
        assert "predicted_relevance" in target
        assert "priority" in target
        assert target["priority"] <= 30  # Should be Tier 2-3


def test_build_all_austrian_rss_targets_no_aggregators():
    """Test RSS target building without aggregators."""
    seed_domains = ["techstartup.at"]
    targets = build_all_austrian_rss_targets(seed_domains, include_aggregators=False)
    
    # Should only have company feeds, no aggregators
    aggregator_targets = [t for t in targets if t["source_kind"] == "rss_aggregator"]
    assert len(aggregator_targets) == 0
    
    # Should still have company feeds
    company_targets = [t for t in targets if t["source_kind"] == "rss_company"]
    assert len(company_targets) > 0


def test_build_austrian_company_rss_urls_edge_cases():
    """Test RSS URL building with edge cases."""
    # Test with www prefix
    urls = build_austrian_company_rss_urls("www.techstartup.at")
    assert all(u.startswith("https://www.techstartup.at") or u.startswith("https://techstartup.at") for u in urls)
    
    # Test with scheme prefix (should be handled gracefully)
    urls = build_austrian_company_rss_urls("http://techstartup.at")
    assert all(u.startswith("https://") for u in urls)  # Should convert to https
    
    # Test very short domain
    urls = build_austrian_company_rss_urls("at")
    assert len(urls) > 0  # Should still generate URLs


def test_extract_rss_jobs_mixed_content():
    """Test extraction with mixed RSS/HTML content."""
    # Test that it still works with extra whitespace
    rss_with_extra = "  \n  " + RSS_AUSTRIAN_COMPANY + "  \n  "
    jobs = extract_rss_jobs(rss_with_extra)
    assert len(jobs) == 2