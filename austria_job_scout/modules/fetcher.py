"""fetcher — Target → RawResponse + fetch_log (iter-2, working).

Honours every Pillar 0 + Pillar 0b constraint by calling the orchestrator
in this exact order for each target:

  1. AGGRESSIVE_MODE gate — if False and target is WAF-protected, BLOCK
  2. circuit_breaker check — if domain is in cool-off, BLOCK
  3. daily_budget check — if today's budget exhausted, RAISE
  4. fetch_log dedupe — if URL fetched recently, return cached
  5. random.uniform(DELAY_MIN, DELAY_MAX) sleep + 15% long-pause
  6. Navigation noise — first hit to a domain is GET /, then GET /jobs, then target
  7. The actual HTTP call (jrf stealth_fetch or vanilla requests)
  8. fetch_log + daily_request_counter write (atomically)
  9. On 4xx/5xx — record detection_event, possibly trip circuit_breaker

Side-effects recorded in DB:
  - fetch_log: one row per network attempt (or cached hit)
  - daily_request_counter: increments per UTC day
  - circuit_breaker: consecutive_failures++ on errors
  - detection_events: anti-bot-three-pillars audit trail

This module is the single most safety-critical module in the project.
Any change must be reviewed against PITFALLS.md Pillars 0 + 0b.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

from .. import config, db
from ..probes import ats_classifier

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class FetchError(Exception):
    """Base class for all fetcher errors."""


class CFProtectedSiteError(FetchError):
    """The target is on the WAF blocklist and AGGRESSIVE_MODE is off."""


class CircuitOpenError(FetchError):
    """The target's domain circuit breaker is open (cool-off active)."""


class DailyBudgetExhausted(FetchError):
    """The UTC-day request budget is exhausted for this site class."""


class MaxFetchesPerRunExceeded(FetchError):
    """The per-run cap was hit mid-batch. Caller should wishlist the rest."""


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------

@dataclass
class RawResponse:
    """One HTTP response (or cached entry)."""
    url: str
    status_code: int | None
    text: str | None
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: int = 0
    cached: bool = False                 # True if served from fetch_log
    error: str | None = None
    ats_fingerprint: str = "unknown"
    fetched_at: int = 0
    # Was this request blocked before reaching the network?
    blocked_reason: str | None = None


# ---------------------------------------------------------------------------
# Sidecar: jrf stealth_fetch (preferred) or vanilla requests
# ---------------------------------------------------------------------------

try:
    _JRF_PATH = Path.home() / ".hermes/projects/job-research-framework"
    if _JRF_PATH.exists() and str(_JRF_PATH) not in sys.path:
        sys.path.insert(0, str(_JRF_PATH))
    from scripts.stealth_fetch import stealth_fetch as _jrf_stealth_fetch  # type: ignore
    _HAS_JRF = True
except Exception as _e:   # ImportError, but also if jrf is broken
    _jrf_stealth_fetch = None
    _HAS_JRF = False
    logger.debug("jrf stealth_fetch not available: %s", _e)


def _http_get(url: str, timeout: int = 30, impersonate: Optional[str] = "chrome120") -> tuple:
    """One HTTP GET. Returns (text, status_code, error)."""
    if _HAS_JRF and _jrf_stealth_fetch is not None:
        return _jrf_stealth_fetch(
            url, timeout=timeout, impersonate=impersonate,
        )
    # Vanilla fallback
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        return (resp.text, resp.status_code, None)
    except requests.RequestException as e:
        return (None, None, str(e))


# ---------------------------------------------------------------------------
# DB helpers (internal)
# ---------------------------------------------------------------------------

def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _host(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    if h.startswith("www."):
        h = h[4:]
    return h


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _fetch_log_get(conn, url: str) -> dict | None:
    """Return the fetch_log row for a URL, or None."""
    h = _url_hash(url)
    row = conn.execute(
        "SELECT * FROM fetch_log WHERE url_hash=?", (h,)
    ).fetchone()
    return dict(row) if row else None


def _fetch_log_upsert(conn, *, url: str, status: int | None, etag: str | None,
                      last_modified: str | None, changed: bool) -> None:
    """Insert or update a fetch_log row. Increments fetch_count on update."""
    h = _url_hash(url)
    now = int(time.time())
    cur = conn.execute("SELECT fetch_count FROM fetch_log WHERE url_hash=?", (h,)).fetchone()
    if cur is None:
        conn.execute(
            """INSERT INTO fetch_log
               (url_hash, url, first_checked, last_checked, last_status,
                last_etag, last_modified, last_changed_at, fetch_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (h, url, now, now, status, etag, last_modified, now if changed else None),
        )
    else:
        conn.execute(
            """UPDATE fetch_log SET
                last_checked=?, last_status=?, last_etag=?, last_modified=?,
                last_changed_at=?, fetch_count=fetch_count+1
               WHERE url_hash=?""",
            (now, status, etag, last_modified, now if changed else None, h),
        )


def _budget_check(conn, url: str) -> None:
    """Raise if today's budget is exhausted. Updates the counter."""
    host = _host(url)
    is_cf = int(config.is_cf_protected(url))
    today = _today_utc()
    # UPSERT counter row
    conn.execute(
        """INSERT INTO daily_request_counter (day_utc, is_cf_site, request_count, last_request_at)
           VALUES (?, ?, 0, NULL)
           ON CONFLICT(day_utc, is_cf_site) DO NOTHING""",
        (today, is_cf),
    )
    row = conn.execute(
        "SELECT request_count FROM daily_request_counter WHERE day_utc=? AND is_cf_site=?",
        (today, is_cf),
    ).fetchone()
    n = int(row["request_count"] or 0)
    cap = config.DAILY_BUDGET_CF_SITE if is_cf else config.daily_budget()
    if n >= cap:
        raise DailyBudgetExhausted(
            f"daily budget exhausted for {today} is_cf={is_cf}: {n}/{cap}"
        )


def _budget_increment(conn, url: str) -> None:
    today = _today_utc()
    is_cf = int(config.is_cf_protected(url))
    conn.execute(
        """INSERT INTO daily_request_counter (day_utc, is_cf_site, request_count, last_request_at)
           VALUES (?, ?, 1, ?)
           ON CONFLICT(day_utc, is_cf_site) DO UPDATE SET
             request_count = request_count + 1,
             last_request_at = excluded.last_request_at""",
        (today, is_cf, int(time.time())),
    )


def _circuit_check(conn, url: str) -> None:
    """Raise if the domain's circuit breaker is in cool-off."""
    host = _host(url)
    row = conn.execute("SELECT * FROM circuit_breaker WHERE domain=?", (host,)).fetchone()
    if not row:
        return
    until = row["cooldown_until"] or 0
    if until and until > int(time.time()):
        raise CircuitOpenError(
            f"circuit breaker open for {host} until {until} "
            f"({int(until - time.time())}s remaining)"
        )


def _circuit_record(conn, url: str, status: int | None, error: str | None) -> None:
    """Update circuit_breaker for this domain. Trip on errors."""
    host = _host(url)
    now = int(time.time())
    is_error = status is None or status >= 400
    cur = conn.execute("SELECT consecutive_failures, total_failures FROM circuit_breaker WHERE domain=?", (host,)).fetchone()
    if cur is None:
        conn.execute(
            """INSERT INTO circuit_breaker
               (domain, consecutive_failures, last_failure_at, last_status_code,
                last_error, opened_at, cooldown_until, total_attempts, total_failures)
               VALUES (?, ?, ?, ?, ?, NULL, NULL, 1, ?)""",
            (host, 1 if is_error else 0, now if is_error else None,
             status, error, 1 if is_error else 0),
        )
    else:
        new_consec = (int(cur["consecutive_failures"]) + 1) if is_error else 0
        cooldown = None
        opened_at = None
        if new_consec >= config.CIRCUIT_BREAKER_THRESHOLD:
            cooldown = now + config.CIRCUIT_BREAKER_COOLDOWN_S
            opened_at = now
        conn.execute(
            """UPDATE circuit_breaker SET
                consecutive_failures = ?,
                last_failure_at = CASE WHEN ? THEN ? ELSE last_failure_at END,
                last_status_code = ?, last_error = ?,
                opened_at = COALESCE(?, opened_at),
                cooldown_until = ?,
                total_attempts = total_attempts + 1,
                total_failures = total_failures + ?
               WHERE domain=?""",
            (new_consec, is_error, now, status, error, opened_at, cooldown,
             1 if is_error else 0, host),
        )


def _record_detection(conn, url: str, status: int | None, error: str | None) -> None:
    """Append to detection_events for audit (Pillar 0 rule 6)."""
    if status is None or status < 400:
        return
    host = _host(url)
    pillar = "behavioral" if status == 429 else "tls" if status in {403, 401} else "browser"
    conn.execute(
        """INSERT INTO detection_events
           (domain, url, pillar, signal, response_code, occurred_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (host, url, pillar,
         error or f"HTTP {status}",
         status,
         int(time.time())),
    )


# ---------------------------------------------------------------------------
# Per-target fetch (network call)
# ---------------------------------------------------------------------------

def _sleep_like_human() -> None:
    """Apply the configured long-tail delay + 15% chance of long pause."""
    delay = random.uniform(config.DELAY_MIN_S, config.DELAY_MAX_S)
    time.sleep(delay)
    if random.random() < config.LONG_PAUSE_PROB:
        long_pause = random.uniform(config.LONG_PAUSE_MIN_S, config.LONG_PAUSE_MAX_S)
        logger.info("long-pause %ds (human-like)", int(long_pause))
        time.sleep(long_pause)


def _navigation_noise(url: str) -> None:
    """For first-ever request to a host: GET /, then GET /jobs, then the target.
    Synthesises a plausible Referer chain. Cheap (30s timeouts)."""
    host = _host(url)
    # GET /
    _http_get(f"https://{host}/", timeout=15)
    time.sleep(random.uniform(2, 6))
    # GET /jobs (or first career path on this host)
    _http_get(f"https://{host}/jobs", timeout=15)


def _fetch_one(target: dict, *, db_path: Optional[Path] = None,
               navigation_noise: bool = True) -> RawResponse:
    """Fetch a single target, honouring every Pillar 0 + 0b guard."""
    url = target["url"]

    # Guard 1: AGGRESSIVE_MODE + is_cf_protected
    if not config.AGGRESSIVE_MODE and config.is_cf_protected(url):
        msg = f"blocked: {url} is WAF-protected and AGGRESSIVE_MODE is off"
        logger.warning(msg)
        return RawResponse(url=url, status_code=None, text=None,
                          cached=False, error=None, blocked_reason=msg)

    path = Path(db_path) if db_path else None
    with db.get_conn_ctx(path) as conn:
        # Guard 2: circuit breaker
        try:
            _circuit_check(conn, url)
        except CircuitOpenError as e:
            return RawResponse(url=url, status_code=None, text=None,
                              error=str(e), blocked_reason="circuit_open")

        # Guard 3: daily budget
        try:
            _budget_check(conn, url)
        except DailyBudgetExhausted as e:
            # Budget exhaustion is fatal for this run — caller must wishlist rest
            raise

        # Guard 4: dedupe (return cached if recent)
        row = _fetch_log_get(conn, url)
        if row and (int(time.time()) - int(row["last_checked"])) < config.DEDUPE_TTL_S:
            return RawResponse(
                url=url, status_code=row["last_status"],
                text=None,   # body not stored; downstream fetches can re-GET if needed
                cached=True,
                fetched_at=int(row["last_checked"]),
            )

        # Guard 5: navigation noise (only if first-ever hit to this host)
        if navigation_noise and not row:
            try:
                _navigation_noise(url)
            except Exception as e:
                logger.debug("navigation noise failed for %s: %s", url, e)

        # Guard 6: human-like delay
        _sleep_like_human()

    # Actual HTTP — outside the connection block so we don't hold a write txn
    started = int(time.monotonic() * 1000)
    text, status, error = _http_get(url)
    elapsed = int(time.monotonic() * 1000) - started

    # Record outcome
    with db.get_conn_ctx(path) as conn:
        _budget_increment(conn, url)
        _fetch_log_upsert(conn, url=url, status=status, etag=None,
                          last_modified=None, changed=True)
        _circuit_record(conn, url, status, error)
        _record_detection(conn, url, status, error)

    fingerprint = ats_classifier.classify(url, text or "")
    return RawResponse(
        url=url, status_code=status, text=text, elapsed_ms=elapsed,
        cached=False, error=error,
        ats_fingerprint=fingerprint, fetched_at=int(time.time()),
    )


def _wishlist_write(conn, targets: list[dict], reference_id: Optional[int] = 0) -> int:
    """Write overflow/blocked targets to the wishlist table.

    Idempotent: INSERT OR IGNORE on (reference_id, url).
    Returns the number of rows inserted.
    """
    if not targets:
        return 0
    now = int(time.time())
    # FK constraint: reference_id must exist in reference_jobs, or be NULL.
    # Use None when reference_id=0 (the default — no reference job persisted).
    ref_id = reference_id if reference_id and reference_id > 0 else None
    inserted = 0
    for t in targets:
        url = t.get("url") or ""
        if not url:
            continue
        url_hash = _url_hash(url)
        cur = conn.execute(
            """INSERT OR IGNORE INTO wishlist
               (reference_id, url, url_hash, source_kind, ats, company_name,
                predicted_relevance, wishlisted_at, wishlist_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                ref_id,
                url,
                url_hash,
                t.get("source_kind", "unknown"),
                t.get("ats"),
                t.get("company_name"),
                t.get("predicted_relevance", 0.0),
                now,
            ),
        )
        inserted += cur.rowcount
    return inserted


def load_wishlist(
    db_path: Optional[Path] = None,
    reference_id: Optional[int] = 0,
    limit: int = 50,
) -> list[dict]:
    """Load pending wishlist targets for the next run.

    Returns targets sorted by predicted_relevance DESC, ready to pass
    to fetch().
    """
    path = Path(db_path) if db_path else None
    out: list[dict] = []
    ref_id = reference_id if reference_id and reference_id > 0 else None
    with db.get_conn_ctx(path) as conn:
        if ref_id is not None:
            rows = conn.execute(
                """SELECT url, source_kind, ats, company_name,
                          predicted_relevance, reference_id
                   FROM wishlist
                   WHERE wishlist_status = 'pending' AND reference_id = ?
                   ORDER BY predicted_relevance DESC
                   LIMIT ?""",
                (ref_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT url, source_kind, ats, company_name,
                          predicted_relevance, reference_id
                   FROM wishlist
                   WHERE wishlist_status = 'pending'
                   ORDER BY predicted_relevance DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        for r in rows:
            out.append({
                "url": r["url"],
                "source_kind": r["source_kind"],
                "ats": r["ats"] or "unknown",
                "company_name": r["company_name"],
                "predicted_relevance": r["predicted_relevance"] or 0.5,
                "priority": config.SOURCE_PRIORITY.get(r["ats"] or "", 30),
                "from_wishlist": True,
            })
    return out


def mark_wishlist_fetched(
    db_path: Optional[Path],
    urls: list[str],
) -> None:
    """Mark wishlist items as fetched after a successful pipeline run."""
    if not urls:
        return
    path = Path(db_path) if db_path else None
    now = int(time.time())
    with db.get_conn_ctx(path) as conn:
        for url in urls:
            conn.execute(
                """UPDATE wishlist
                   SET wishlist_status = 'fetched', fetched_at = ?
                   WHERE url = ? AND wishlist_status = 'pending'""",
                (now, url),
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch(targets: list[dict], *, db_path: Optional[Path] = None,
          navigation_noise: bool = True,
          max_fetches: int | None = None,
          reference_id: Optional[int] = 0) -> list[RawResponse]:
    """Fetch a list of targets, in priority order. Honours per-run cap.

    Stops early if a target raises DailyBudgetExhausted — caller is expected
    to wishlist the remainder and continue tomorrow.

    Blocked targets (max_fetches reached, budget exhausted, exceptions) are
    persisted to the ``wishlist`` table via ``_wishlist_write`` so the next
    run can pick them up via ``load_wishlist``.
    """
    cap = max_fetches if max_fetches is not None else config.MAX_FETCH_PER_RUN
    out: list[RawResponse] = []
    blocked: list[dict] = []

    for i, target in enumerate(targets):
        if len(out) >= cap:
            logger.info("max_fetches=%d reached; wishlisting the rest", cap)
            blocked.append({**target, "_reason": "max_fetches_reached"})
            continue
        try:
            r = _fetch_one(target, db_path=db_path, navigation_noise=navigation_noise)
        except DailyBudgetExhausted as e:
            logger.warning("daily budget hit; aborting: %s", e)
            # Collect remaining targets for wishlist
            remaining = targets[i:]
            for t in remaining:
                blocked.append({**t, "_reason": "daily_budget_exhausted", "_detail": str(e)})
            # Persist all blocked to wishlist
            path = Path(db_path) if db_path else None
            with db.get_conn_ctx(path) as conn:
                _wishlist_write(conn, blocked, reference_id=reference_id)
            fetch.last_blocked = blocked   # type: ignore[attr-defined]
            raise
        except Exception as e:
            logger.exception("fetcher crashed for %s", target.get("url"))
            blocked.append({**target, "_reason": "exception", "_detail": str(e)})
            continue
        out.append(r)

    # Persist blocked targets to wishlist table
    if blocked:
        path = Path(db_path) if db_path else None
        try:
            with db.get_conn_ctx(path) as conn:
                count = _wishlist_write(conn, blocked, reference_id=reference_id)
                if count:
                    logger.info("persisted %d targets to wishlist", count)
        except Exception as e:
            logger.warning("failed to write wishlist: %s", e)

    fetch.last_blocked = blocked   # type: ignore[attr-defined]
    return out


# Initialise the side attribute on first import
fetch.last_blocked = []   # type: ignore[attr-defined]
