"""Tests for the Phase 6.1 KMU career-URL wire-up helper.

These tests exercise the same `_normalize_domain` style that
`/srv/sync/company-recheck-2026-07/scripts/kmu_career_urls.py` uses,
ensuring the library contract (`build_kmu_career_urls`) is robust
against the artefacts we see from the day-1 scout runs.

If `scripts/kmu_career_urls.py` is later moved into the package
proper, these tests should be migrated alongside it.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from austria_job_scout.modules.kmu_wien_discovery import build_kmu_career_urls
from austria_job_scout.seeds import SeedCompany


# Mirror of `kmu_career_urls._normalize_domain`. Kept in sync by hand
# to avoid a package import — the script lives in a scratch directory.
_NORMALIZE_RE = re.compile(r"^(?:https?://)?(?:www\.)?(?P<host>[^/?#]+)")
_REJECT_HOSTS = {"nan", "none", "null", ""}


def normalize_domain(raw: str | None) -> str:
    """Apex-domain normaliser that rejects sentinel values like 'nan'."""
    if not raw:
        return ""
    s = raw.strip().lower()
    if not s or s in _REJECT_HOSTS:
        return ""
    m = _NORMALIZE_RE.match(s)
    if not m:
        return ""
    host = m.group("host")
    if host in _REJECT_HOSTS or "." not in host:
        return ""
    return host


class TestNormalizeDomain:
    def test_strips_https_www(self):
        assert normalize_domain("https://www.example.com/foo") == "example.com"

    def test_strips_scheme_only(self):
        assert normalize_domain("http://example.com") == "example.com"

    def test_lowercases(self):
        assert normalize_domain("WWW.Example.COM") == "example.com"

    def test_rejects_nan_literal(self):
        assert normalize_domain("nan") == ""
        assert normalize_domain("NaN") == ""
        assert normalize_domain("https://nan") == ""
        assert normalize_domain("https://nan/jobs") == ""

    def test_rejects_none_and_null(self):
        assert normalize_domain("none") == ""
        assert normalize_domain("null") == ""

    def test_rejects_empty(self):
        assert normalize_domain("") == ""
        assert normalize_domain(None) == ""

    def test_rejects_tldless(self):
        # bare words without a dot are not real domains
        assert normalize_domain("buongiorno") == ""

    def test_accepts_real_apex(self):
        assert normalize_domain("netavis.net") == "netavis.net"
        assert normalize_domain("ara.at") == "ara.at"

    def test_drops_query_string(self):
        assert normalize_domain("example.com?foo=bar") == "example.com"


class TestBuildKmuCareerUrls:
    """The 6.1 wire-up uses this library function as the URL expander."""

    def test_yields_sixteen_urls(self):
        seed = SeedCompany(name="Example GmbH", domain="example.com")
        urls = build_kmu_career_urls(seed)
        assert len(urls) == 16

    def test_all_urls_are_https(self):
        seed = SeedCompany(name="Example", domain="example.com")
        for u in build_kmu_career_urls(seed):
            assert u.startswith("https://"), u

    def test_includes_karriere_jobs_careers_paths(self):
        seed = SeedCompany(name="Example", domain="example.com")
        urls = build_kmu_career_urls(seed)
        joined = " ".join(urls)
        # Audit mirrors the source's path list
        assert "/karriere" in joined
        assert "/jobs" in joined
        assert "/careers" in joined
        assert "/stellenangebote" in joined

    def test_includes_jobs_and_careers_subdomains(self):
        seed = SeedCompany(name="Example", domain="example.com")
        urls = build_kmu_career_urls(seed)
        joined = " ".join(urls)
        assert "jobs.example.com" in joined
        assert "careers.example.com" in joined

    def test_empty_domain_returns_empty_list(self):
        seed = SeedCompany(name="X", domain="")
        assert build_kmu_career_urls(seed) == []


class TestIntegrationWithDay1Artifacts:
    """Sanity-check that the wire-up survives the real fixture shapes."""

    # Cached day-1 sample: a single row that has company_website='https://nan'
    # Must be filtered out before URL expansion.
    SAMPLE_CSV = (
        "row_id,name,company_website\n"
        "1,NETAVIS Software GmbH,https://netavis.net\n"
        "2,Lorem Ipsum,nan\n"
        "3,Demo,https://nan\n"
        "4,ARAX,ara.at\n"
        "5,Empty,\n"
    )

    def test_filters_nan_then_expands(self):
        # Same flow as kmu_career_urls.expand()
        out_urls = []
        for line in self.SAMPLE_CSV.splitlines()[1:]:
            row_id, name, website = line.split(",")
            domain = normalize_domain(website)
            if not domain:
                continue
            urls = build_kmu_career_urls(SeedCompany(name=name, domain=domain))
            out_urls.extend(urls)

        # 2 valid rows × 16 URLs each = 32
        assert len(out_urls) == 32
        # All real domains, no 'nan'
        assert all("nan" not in urlparse(u).netloc for u in out_urls)
        # Real apex domains present
        joined = " ".join(out_urls)
        assert "netavis.net" in joined
        assert "ara.at" in joined
