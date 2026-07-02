"""Tests for career_page_extractor module."""
from __future__ import annotations

import json
import pytest

from austria_job_scout.extractors.career_page_extractor import (
    extract_career_page_jobs,
    extract_json_ld_jobs,
    extract_rss_feed,
    extract_link_based_jobs,
)


# ---------------------------------------------------------------------------
# JSON-LD test fixtures
# ---------------------------------------------------------------------------

JSON_LD_SINGLE_JOB = """
<!DOCTYPE html>
<html>
<head>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Senior Software Engineer",
        "description": "We are looking for an experienced Python developer...",
        "hiringOrganization": {
            "@type": "Organization",
            "name": "Tech Startup GmbH"
        },
        "jobLocation": {
            "address": {
                "addressLocality": "Wien"
            }
        },
        "datePosted": "2024-07-01"
    }
    </script>
</head>
<body>
    <h1>Karriere</h1>
    <p>Wir suchen talentierte Entwickler...</p>
</body>
</html>
"""

JSON_LD_MULTIPLE_JOBS = """
<!DOCTYPE html>
<html>
<head>
    <script type="application/ld+json">
    [
        {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Frontend Developer",
            "hiringOrganization": {"name": "Web Agency Wien"},
            "jobLocation": {
                "address": {
                    "addressLocality": "Wien"
                }
            }
        },
        {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Backend Developer",
            "hiringOrganization": {"name": "Web Agency Wien"},
            "jobLocation": {
                "address": {
                    "addressLocality": "Wien"
                }
            }
        }
    ]
    </script>
</head>
<body>
    <h1>Offene Stellen</h1>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# RSS test fixtures
# ---------------------------------------------------------------------------

RSS_SINGLE_JOB = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
    <channel>
        <title>Test Company Jobs</title>
        <description>Job listings at Test Company</description>
        <item>
            <title>Software Engineer</title>
            <link>https://test.com/jobs/123</link>
            <description>We are looking for a talented developer...</description>
            <pubDate>Mon, 01 Jul 2024 09:00:00 +0000</pubDate>
        </item>
    </channel>
</rss>"""

RSS_ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
    <title>Test Company Jobs</title>
    <link href="https://test.com/jobs" />
    <entry>
        <title>DevOps Engineer</title>
        <link href="https://test.com/jobs/456" />
        <summary>Looking for infrastructure engineers...</summary>
        <published>2024-07-02T10:00:00Z</published>
    </entry>
</feed>"""

# ---------------------------------------------------------------------------
# Link-based extraction fixtures
# ---------------------------------------------------------------------------

HTML_WITH_JOB_LINKS = """
<!DOCTYPE html>
<html>
<body>
    <h1>Karriere bei Tech Startup GmbH</h1>
    <p>Wir bieten interessante Stellen:</p>
    <div class="job-listing">
        <a href="/jobs/frontend-developer">Frontend Developer (React) gesucht</a>
        <a href="/jobs/backend-developer">Backend Developer (Python) gesucht</a>
        <a href="/jobs/devops">DevOps Engineer gesucht</a>
    </div>
    <p>Mehr Informationen: <a href="/jobs/alle-jobs">alle anzeigen</a></p>
    <nav>
        <a href="/">Zurück zur Startseite</a>
    </nav>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_extract_json_ld_jobs_single():
    """Test JSON-LD extraction for single job posting."""
    jobs = extract_json_ld_jobs(JSON_LD_SINGLE_JOB)
    assert len(jobs) == 1
    assert jobs[0].source == "career_json_ld"
    assert jobs[0].title == "Senior Software Engineer"
    assert jobs[0].company == "Tech Startup GmbH"
    assert jobs[0].location == "Wien"
    assert "Python" in jobs[0].skills


def test_extract_json_ld_jobs_multiple():
    """Test JSON-LD extraction for multiple job postings."""
    jobs = extract_json_ld_jobs(JSON_LD_MULTIPLE_JOBS)
    assert len(jobs) == 2
    
    titles = [j.title for j in jobs]
    assert "Frontend Developer" in titles
    assert "Backend Developer" in titles
    
    # All jobs should have same company
    for job in jobs:
        assert job.company == "Web Agency Wien"
        assert job.location == "Wien"


def test_extract_rss_feed_rss2():
    """Test RSS 2.0 feed extraction."""
    jobs = extract_rss_feed(RSS_SINGLE_JOB)
    assert len(jobs) == 1
    
    job = jobs[0]
    assert job.title == "Software Engineer"
    assert job.url == "https://test.com/jobs/123"
    assert "developer" in job.description.lower()
    assert job.posted_date == "Mon, 01 Jul 2024 09:00:00 +0000"


def test_extract_rss_feed_atom():
    """Test Atom feed extraction."""
    jobs = extract_rss_feed(RSS_ATOM_FEED)
    assert len(jobs) == 1
    
    job = jobs[0]
    assert job.title == "DevOps Engineer"
    assert job.url == "https://test.com/jobs/456"
    assert "infrastructure" in job.description.lower()


def test_extract_rss_feed_invalid_xml():
    """Test RSS extraction with invalid XML."""
    invalid_xml = "<invalid>xml content</not-valid>"
    jobs = extract_rss_feed(invalid_xml)
    assert jobs == []


def test_extract_link_based_jobs():
    """Test link-based job extraction."""
    jobs = extract_link_based_jobs(HTML_WITH_JOB_LINKS, "https://test.com/", "Tech Startup GmbH")
    assert len(jobs) == 3
    
    titles = [j.title for j in jobs]
    assert "Frontend Developer (React) gesucht" in titles
    assert "Backend Developer (Python) gesucht" in titles
    assert "DevOps Engineer gesucht" in titles
    
    # All jobs should have proper URLs and company
    for job in jobs:
        assert job.company == "Tech Startup GmbH"
        assert job.source == "career_link"


def test_extract_link_based_jobs_navigation_links_filtered():
    """Test that navigation/utility links are filtered out."""
    html_with_nav = """
    <html>
    <body>
        <a href="/jobs/alle-jobs">alle anzeigen</a>
        <a href="/jobs/next">weiter</a>
        <a href="/jobs/frontend">Frontend Developer</a>
    </body>
    </html>
    """
    
    jobs = extract_link_based_jobs(html_with_nav, "https://test.com/")
    assert len(jobs) == 1
    assert jobs[0].title == "Frontend Developer"


def test_extract_career_page_jobs_json_ld_priority():
    """Test JSON-LD extraction takes priority over other strategies."""
    # Test with JSON-LD content (should use JSON-LD, not RSS)
    jobs = extract_career_page_jobs(JSON_LD_SINGLE_JOB, "https://test.com/")
    assert len(jobs) == 1
    assert jobs[0].source == "career_json_ld"
    assert jobs[0].title == "Senior Software Engineer"


def test_extract_career_page_jobs_rss_detection():
    """Test that RSS content is detected and extracted."""
    # Test with RSS content as if it were HTML
    # The function should detect it's XML and use RSS extraction
    jobs = extract_career_page_jobs(RSS_SINGLE_JOB, "https://test.com/")
    assert len(jobs) == 1
    assert jobs[0].title == "Software Engineer"


def test_extract_career_page_jobs_fallback_to_links():
    """Test fallback to link extraction when no structured data."""
    # Test with plain HTML containing job links
    jobs = extract_career_page_jobs(HTML_WITH_JOB_LINKS, "https://test.com/")
    assert len(jobs) == 3
    assert all(job.source == "career_link" for job in jobs)


def test_extract_career_page_jobs_empty_content():
    """Test extraction with empty content."""
    jobs = extract_career_page_jobs("", "https://test.com/")
    assert jobs == []


def test_extract_career_page_jobs_company_param():
    """Test that company name is passed through correctly."""
    jobs = extract_career_page_jobs(HTML_WITH_JOB_LINKS, "https://test.com/", "Test Company")
    for job in jobs:
        assert job.company == "Test Company"


def test_extract_json_ld_jobs_no_ld():
    """Test JSON-LD extraction with no script tags."""
    plain_html = "<html><body><h1>Karriere</h1></body></html>"
    jobs = extract_json_ld_jobs(plain_html)
    assert jobs == []


def test_extract_link_based_jobs_bad_links():
    """Test link-based extraction filters out invalid links."""
    html_bad_links = """
    <html>
    <body>
        <a href="#top">Top</a>
        <a href="javascript:void(0)">Invalid</a>
        <a href="">Empty</a>
        <a href="/jobs/valid">Valid Job</a>
    </body>
    </html>
    """
    
    jobs = extract_link_based_jobs(html_bad_links, "https://test.com/")
    assert len(jobs) == 1
    assert jobs[0].title == "Valid Job"


def test_extract_link_based_jobs_no_duplicate_urls():
    """Test link-based extraction removes duplicate URLs."""
    html_duplicates = """
    <html>
    <body>
        <a href="/jobs/test">Test Job 1</a>
        <a href="/jobs/test">Test Job 2 (duplicate)</a>
        <a href="/jobs/other">Other Job</a>
    </body>
    </html>
    """
    
    jobs = extract_link_based_jobs(html_duplicates, "https://test.com/")
    # Should have only unique URLs
    urls = [j.url for j in jobs]
    assert len(urls) == len(set(urls))  # No duplicates