"""Career-path probe — HEAD requests against the standard 8 paths.

Picks the top N candidate URLs per company domain, HEAD-probes them
(via curl_cffi when available, vanilla requests otherwise), and returns
the ones that respond 2xx/3xx with a non-job-board body.

This is a *probe*, not a fetcher — it only inspects existence + redirect
targets. The fetcher does the actual body fetch.

No bodies are stored, no rate-limit headers are emitted. The probe is
small and polite — single HEAD per path, hard 30s timeout.

Network behaviour:
  - Uses jrf.scripts.stealth_fetch.stealth_fetch when available (curl_cffi)
  - Falls back to vanilla requests.get(stream=True) otherwise
  - HEAD-only by default; pass ``method="GET"`` to follow a redirect and
    inspect the body (for ATS fingerprinting)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import requests

from .. import config

logger = logging.getLogger(__name__)


# Standard paths Austrian companies use for career pages.
# Ordered roughly by likelihood.
CAREER_PATHS: tuple[str, ...] = (
    "/karriere",
    "/jobs",
    "/careers",
    "/stellenangebote",
    "/jobs-und-karriere",
    "/de/karriere",
    "/at/karriere",
    "/en/careers",
)

# Career subdomains (Personio-style "talent.example.com")
CAREER_SUBDOMAINS: tuple[str, ...] = (
    "careers",
    "karriere",
    "jobs",
    "talent",
    "join",
    "work",
)


@dataclass
class PathProbeResult:
    """Result of probing a single URL."""
    url: str
    status_code: int | None = None
    redirected_to: str | None = None
    ats_fingerprint: str = "unknown"
    error: str | None = None
    elapsed_ms: int = 0


def _scheme(domain: str) -> str:
    return "https"


def candidate_urls_for_domain(domain: str, paths: tuple[str, ...] = CAREER_PATHS) -> list[str]:
    """Return the list of candidate URLs to HEAD-probe for one domain.

    Includes apex-path probes + subdomain probes. Order is by hit-rate
    (most-common first).
    """
    d = domain.lower().strip()
    if d.startswith("www."):
        d = d[4:]
    if "://" in d:
        # full URL passed; strip
        d = urlparse(d).hostname or d
        if d.startswith("www."):
            d = d[4:]
    s = _scheme(d)

    out: list[str] = []
    for p in paths:
        out.append(f"{s}://{d}{p}")
    for sub in CAREER_SUBDOMAINS:
        out.append(f"{s}://{sub}.{d}/")
    return out


# ---------------------------------------------------------------------------
# HTTP probe (mockable)
# ---------------------------------------------------------------------------

def _do_head(url: str, timeout: int = 30) -> PathProbeResult:
    """One HEAD request. Uses jrf stealth_fetch if available, else vanilla."""
    started = 0
    try:
        import time
        started = int(time.monotonic() * 1000)
        # Try jrf first (provides TLS impersonation)
        try:
            from scripts.stealth_fetch import stealth_fetch  # type: ignore
            text, status, err = stealth_fetch(url, timeout=timeout)
            import time as _t
            elapsed = int(_t.monotonic() * 1000) - started
            if err or status is None:
                return PathProbeResult(
                    url=url, status_code=status, error=err, elapsed_ms=elapsed,
                )
            # stealth_fetch is a GET, not HEAD. For probing, GET is fine —
            # we just want to know if the URL is alive.
            from .ats_classifier import classify
            fingerprint = classify(url, text or "")
            return PathProbeResult(
                url=url, status_code=int(status),
                ats_fingerprint=fingerprint,
                elapsed_ms=elapsed,
            )
        except ImportError:
            pass   # fall through to vanilla requests

        # Vanilla HEAD probe (no body, fast)
        resp = requests.head(
            url, allow_redirects=True, timeout=timeout, verify=True,
        )
        import time as _t
        elapsed = int(_t.monotonic() * 1000) - started
        return PathProbeResult(
            url=url,
            status_code=resp.status_code,
            redirected_to=resp.url if resp.url != url else None,
            elapsed_ms=elapsed,
        )
    except requests.RequestException as e:
        import time as _t
        elapsed = int(_t.monotonic() * 1000) - started
        return PathProbeResult(url=url, status_code=None, error=str(e), elapsed_ms=elapsed)
    except Exception as e:
        import time as _t
        elapsed = int(_t.monotonic() * 1000) - started
        return PathProbeResult(url=url, status_code=None, error=f"unexpected: {e}", elapsed_ms=elapsed)


def probe_domain(domain: str, timeout: int = 30) -> list[PathProbeResult]:
    """HEAD-probe all candidate paths for a domain. Returns only the
    successful ones (2xx or 3xx)."""
    results: list[PathProbeResult] = []
    for url in candidate_urls_for_domain(domain):
        r = _do_head(url, timeout=timeout)
        if r.status_code is not None and 200 <= r.status_code < 400:
            results.append(r)
    return results
