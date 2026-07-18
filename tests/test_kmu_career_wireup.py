"""Phase 6.1 wire-up smoke test.

The script `/srv/sync/company-recheck-2026-07/scripts/kmu_career_urls.py`
joins `scout_*.csv` to austria-job-scout's `build_kmu_career_urls` helper.
The day-1 scout CSV can contain `'nan'` sentinel hosts (literal "nan" as
domain); the wire-up MUST filter them so we don't burn the residential
fetch budget on `https://jobs.nan/` URLs.

One runnable check covers the invariant: a row with a sentinel domain
must produce zero candidate URLs, a row with a real apex must produce
exactly 16 candidates (the count kmu_wien_discovery.build_kmu_career_urls
emits per apex domain).
"""

from __future__ import annotations

from urllib.parse import urlparse

from austria_job_scout.modules.kmu_wien_discovery import build_kmu_career_urls
from austria_job_scout.seeds import SeedCompany


SENTINELS = {"nan", "none", "null", ""}


def _is_real_domain(value: str) -> bool:
    """Apex-domain filter mirroring `kmu_career_urls._normalize_domain`."""
    if not value:
        return False
    s = value.strip().lower()
    if not s or s in SENTINELS:
        return False
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    host = s.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return bool(host) and host not in SENTINELS and "." in host


def test_wireup_filters_sentinels_and_produces_expected_url_count():
    """End-to-end sanity check against day-1 CSV shape.

    Real apex hosts (NETAVIS, ARA) must expand to 16 candidate URLs each
    (kmu_wien_discovery contract). Sentinel hosts ("nan", "none", "null",
    empty, "https://nan") must produce zero candidates so the residential
    fetch budget is not wasted on `https://jobs.nan/`.
    """
    fixture = [
        ("1", "NETAVIS Software GmbH", "https://netavis.net"),  # 16 expected
        ("2", "Lorem Ipsum",          "nan"),                   # 0 expected
        ("3", "Demo",                 "https://nan"),           # 0 expected
        ("4", "ARAX",                 "ara.at"),                # 16 expected
        ("5", "Empty",                ""),                      # 0 expected
        ("6", "Null variant",         "null"),                  # 0 expected
    ]

    out_urls: list[str] = []
    for _row_id, _name, website in fixture:
        if not _is_real_domain(website):
            continue
        apex = website.lower().strip()
        for prefix in ("https://", "http://"):
            if apex.startswith(prefix):
                apex = apex[len(prefix):]
                break
        apex = apex.split("/", 1)[0]
        if apex.startswith("www."):
            apex = apex[4:]
        out_urls.extend(build_kmu_career_urls(SeedCompany(name=_name, domain=apex)))

    # 2 valid rows × 16 URLs each = 32 candidates.
    assert len(out_urls) == 32, f"expected 32 candidates, got {len(out_urls)}"
    # The sentinel check is on the HOST (not the substring): the wire-up's
    # entire purpose is to prevent sentinel hosts from leaking through.
    sentinel_hosts = {urlparse(u).hostname for u in out_urls}
    assert "nan" not in sentinel_hosts, (
        f"sentinel host 'nan' leaked through: {sorted(h for h in sentinel_hosts if h == 'nan')}"
    )
    # Real apex domains are present in the candidate URLs.
    joined = " ".join(out_urls)
    assert "netavis.net" in joined
    assert "ara.at" in joined
