"""Tests for the fetcher — all network I/O mocked.

The fetcher is the single most safety-critical module. Tests focus on:
  - Pillar 0 enforcement: AGGRESSIVE_MODE, CF blocklist, circuit breaker, daily budget
  - Pillar 0b enforcement: MAX_FETCH_PER_RUN, wishlist remainder
  - fetch_log dedupe (returns cached on repeat)
  - Honest error reporting
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from austria_job_scout import config, db
from austria_job_scout.modules import fetcher as fetcher_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_targets():
    return [
        {
            "ats": "greenhouse",
            "source_kind": "ats_board",
            "url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
            "company_name": "Acme",
            "company_domain": "acme.com",
            "predicted_relevance": 0.9,
            "priority": 10,
            "notes": "test",
        },
        {
            "ats": "karriere_at",
            "source_kind": "aggregator_query",
            "url": "https://www.karriere.at/jobs?q=rust",
            "company_name": None,
            "company_domain": None,
            "predicted_relevance": 0.7,
            "priority": 25,
            "notes": "test",
        },
    ]


def _mock_http_ok(url, **_kw):
    return (f"<html>{url}</html>", 200, None)


def _mock_http_403(url, **_kw):
    return (None, 403, "Forbidden")


# ---------------------------------------------------------------------------
# Guard 1: AGGRESSIVE_MODE + CF blocklist
# ---------------------------------------------------------------------------

def test_cf_site_blocked_in_residential_mode(tmp_db, monkeypatch, no_sleep):
    monkeypatch.setattr(config, "AGGRESSIVE_MODE", False)
    db.init_db()
    targets = [{
        "ats": "stepstone_at",
        "source_kind": "aggregator_query",
        "url": "https://www.stepstone.at/jobs/senior-engineer",
        "predicted_relevance": 0.9,
        "priority": 90,
        "notes": "WAF",
    }]
    responses = fetcher_mod.fetch(
        targets, db_path=tmp_db, navigation_noise=False, max_fetches=10
    )
    assert len(responses) == 1
    r = responses[0]
    assert r.blocked_reason is not None
    assert "WAF" in r.blocked_reason
    assert r.status_code is None


def test_cf_site_allowed_in_aggressive_mode(tmp_db, monkeypatch, no_sleep):
    monkeypatch.setattr(config, "AGGRESSIVE_MODE", True)
    db.init_db()
    targets = [{
        "ats": "stepstone_at",
        "source_kind": "aggregator_query",
        "url": "https://www.stepstone.at/jobs/senior-engineer",
        "predicted_relevance": 0.9,
        "priority": 90,
        "notes": "WAF",
    }]
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        responses = fetcher_mod.fetch(
            targets, db_path=tmp_db, navigation_noise=False, max_fetches=10
        )
    assert len(responses) == 1
    assert responses[0].blocked_reason is None
    assert responses[0].status_code == 200


# ---------------------------------------------------------------------------
# Guard 2: Circuit breaker
# ---------------------------------------------------------------------------

def test_circuit_breaker_blocks_after_3_failures(tmp_db, monkeypatch, no_sleep):
    db.init_db()
    # Pretend this domain already has 3 consecutive failures and a cool-off in the future.
    # The host is whatever _host() extracts from the URL.
    target = {
        "ats": "greenhouse",
        "source_kind": "ats_board",
        "url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        "company_name": "Acme",
        "company_domain": "acme.com",
        "predicted_relevance": 0.9,
        "priority": 10,
        "notes": "test",
    }
    host = fetcher_mod._host(target["url"])
    with db.get_conn_ctx(tmp_db) as conn:
        conn.execute(
            """INSERT INTO circuit_breaker
               (domain, consecutive_failures, opened_at, cooldown_until,
                total_attempts, total_failures)
               VALUES (?, 3, ?, ?, 3, 3)""",
            (host, int(time.time()), int(time.time()) + 3600),
        )
    targets = [{
        "ats": "greenhouse",
        "source_kind": "ats_board",
        "url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        "company_name": "Acme",
        "company_domain": "acme.com",
        "predicted_relevance": 0.9,
        "priority": 10,
        "notes": "test",
    }]
    responses = fetcher_mod.fetch(
        targets, db_path=tmp_db, navigation_noise=False, max_fetches=10
    )
    # Circuit open → blocked (no network call made)
    assert responses[0].blocked_reason == "circuit_open"
    assert responses[0].status_code is None


def test_circuit_breaker_recovers_after_cooldown(tmp_db, monkeypatch, no_sleep):
    db.init_db()
    target = {
        "ats": "greenhouse",
        "source_kind": "ats_board",
        "url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        "company_name": "Acme",
        "company_domain": "acme.com",
        "predicted_relevance": 0.9,
        "priority": 10,
        "notes": "test",
    }
    past = int(time.time()) - 100  # cool-off ended
    host = fetcher_mod._host(target["url"])
    with db.get_conn_ctx(tmp_db) as conn:
        conn.execute(
            """INSERT INTO circuit_breaker
               (domain, consecutive_failures, opened_at, cooldown_until,
                total_attempts, total_failures)
               VALUES (?, 3, ?, ?, 3, 3)""",
            (host, past, past),
        )
    targets = [{
        "ats": "greenhouse",
        "source_kind": "ats_board",
        "url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        "company_name": "Acme",
        "company_domain": "acme.com",
        "predicted_relevance": 0.9,
        "priority": 10,
        "notes": "test",
    }]
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        responses = fetcher_mod.fetch(
            targets, db_path=tmp_db, navigation_noise=False, max_fetches=10
        )
    # Cooldown is in the past → fetcher proceeds → 200
    assert responses[0].blocked_reason is None
    assert responses[0].status_code == 200


def test_circuit_breaker_trips_after_3_consecutive_errors(tmp_db, monkeypatch, no_sleep):
    db.init_db()
    # 4 targets → 4 failures → after the 3rd, circuit opens; 4th is blocked
    # before reaching the network. But the trip happens at the END of each
    # fetch, so target #4 returns a "circuit_open" blocked_reason.
    targets = [
        {**_seed_targets()[0], "url": f"https://boards-api.greenhouse.io/v1/boards/x{i}/jobs", "company_domain": f"x{i}.com"}
        for i in range(4)
    ]
    # Use one shared mock that returns 403
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_403):
        responses = fetcher_mod.fetch(
            targets, db_path=tmp_db, navigation_noise=False, max_fetches=10
        )
    # After the third 403 on x3.com, the fourth is blocked before network.
    # At least one of the responses must show "circuit_open" (the 4th),
    # and at least one host must have a cooldown_until set.
    with db.get_conn_ctx(tmp_db) as conn:
        cooled = conn.execute(
            "SELECT count(*) AS c FROM circuit_breaker WHERE cooldown_until IS NOT NULL"
        ).fetchone()["c"]
    assert cooled >= 1, "expected at least one host with cooldown set"


def test_daily_budget_exhaustion_blocks_new_requests(tmp_db, monkeypatch, no_sleep):
    db.init_db()
    # Pre-fill the counter for today + normal sites to the cap
    today = time.strftime("%Y-%m-%d", time.gmtime())
    with db.get_conn_ctx(tmp_db) as conn:
        conn.execute(
            """INSERT INTO daily_request_counter (day_utc, is_cf_site, request_count)
               VALUES (?, 0, ?)""",
            (today, config.DAILY_BUDGET_RESIDENTIAL),
        )
    targets = [{
        "ats": "greenhouse",
        "source_kind": "ats_board",
        "url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        "company_name": "Acme",
        "company_domain": "acme.com",
        "predicted_relevance": 0.9,
        "priority": 10,
        "notes": "test",
    }]
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        # Should raise DailyBudgetExhausted
        with pytest.raises(fetcher_mod.DailyBudgetExhausted):
            fetcher_mod.fetch(
                targets, db_path=tmp_db, navigation_noise=False, max_fetches=10
            )


def test_daily_budget_increments_on_success(tmp_db, monkeypatch, no_sleep):
    db.init_db()
    targets = _seed_targets()[:1]
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        fetcher_mod.fetch(
            targets, db_path=tmp_db, navigation_noise=False, max_fetches=10
        )
    today = time.strftime("%Y-%m-%d", time.gmtime())
    with db.get_conn_ctx(tmp_db) as conn:
        n = conn.execute(
            "SELECT request_count FROM daily_request_counter WHERE day_utc=? AND is_cf_site=0",
            (today,),
        ).fetchone()["request_count"]
    assert n == 1


# ---------------------------------------------------------------------------
# Guard 4: Dedupe
# ---------------------------------------------------------------------------

def test_repeat_fetch_returns_cached(tmp_db, monkeypatch, no_sleep):
    db.init_db()
    target = _seed_targets()[0]
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok) as m:
        # First fetch — goes to network
        r1 = fetcher_mod.fetch(
            [target], db_path=tmp_db, navigation_noise=False, max_fetches=10
        )
        # Second fetch — should return cached
        r2 = fetcher_mod.fetch(
            [target], db_path=tmp_db, navigation_noise=False, max_fetches=10
        )
    assert r1[0].cached is False
    assert r1[0].status_code == 200
    assert r2[0].cached is True
    # HTTP was called only once
    assert m.call_count == 1


def test_dedupe_ttl_expires(tmp_db, monkeypatch, no_sleep):
    """If the previous fetch is older than DEDUPE_TTL_S, fetch again."""
    monkeypatch.setattr(config, "DEDUPE_TTL_S", 0)   # always expire
    db.init_db()
    target = _seed_targets()[0]
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        r1 = fetcher_mod.fetch(
            [target], db_path=tmp_db, navigation_noise=False, max_fetches=10
        )
        r2 = fetcher_mod.fetch(
            [target], db_path=tmp_db, navigation_noise=False, max_fetches=10
        )
    assert r1[0].cached is False
    assert r2[0].cached is False   # TTL=0 → re-fetch


# ---------------------------------------------------------------------------
# Guard 7 (Pillar 0b): MAX_FETCH_PER_RUN
# ---------------------------------------------------------------------------

def test_max_fetches_caps_run(tmp_db, monkeypatch, no_sleep):
    monkeypatch.setattr(config, "MAX_FETCH_PER_RUN", 1)
    db.init_db()
    targets = _seed_targets() * 5   # 10 targets
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        responses = fetcher_mod.fetch(
            targets, db_path=tmp_db, navigation_noise=False, max_fetches=1
        )
    assert len(responses) == 1
    # The other 9 should be wishlisted (in last_blocked)
    blocked = fetcher_mod.fetch.last_blocked
    assert any(b.get("_reason") == "max_fetches_reached" for b in blocked)


def test_max_fetches_per_run_writes_wishlist_remainder(tmp_db, monkeypatch, no_sleep):
    """After cap is hit, remaining targets are recorded (TODO: as wishlist
    in iter-3 — for now they're in `last_blocked`)."""
    monkeypatch.setattr(config, "MAX_FETCH_PER_RUN", 2)
    db.init_db()
    targets = _seed_targets() * 3
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        fetcher_mod.fetch(
            targets, db_path=tmp_db, navigation_noise=False, max_fetches=2
        )
    assert len(fetcher_mod.fetch.last_blocked) >= 4


# ---------------------------------------------------------------------------
# Navigation noise
# ---------------------------------------------------------------------------

def test_navigation_noise_skipped_when_disabled(tmp_db, monkeypatch, no_sleep):
    db.init_db()
    target = _seed_targets()[0]
    host = fetcher_mod._host(target["url"])
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok) as m:
        fetcher_mod.fetch(
            [target], db_path=tmp_db, navigation_noise=False, max_fetches=10
        )
    # Only the target URL was fetched, no / or /jobs warm-up
    urls_called = [call.args[0] for call in m.call_args_list]
    assert f"https://{host}/" not in urls_called
    assert f"https://{host}/jobs" not in urls_called


def test_navigation_noise_runs_when_enabled(tmp_db, monkeypatch, no_sleep):
    db.init_db()
    target = _seed_targets()[0]
    host = fetcher_mod._host(target["url"])
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok) as m:
        fetcher_mod.fetch(
            [target], db_path=tmp_db, navigation_noise=True, max_fetches=10
        )
    urls_called = [call.args[0] for call in m.call_args_list]
    # First-ever hit → navigation noise runs (GET / + GET /jobs) + target = 3 calls
    assert f"https://{host}/" in urls_called
    assert f"https://{host}/jobs" in urls_called
    assert target["url"] in urls_called
    assert len(urls_called) >= 3


# ---------------------------------------------------------------------------
# Detection events
# ---------------------------------------------------------------------------

def test_detection_event_recorded_on_403(tmp_db, monkeypatch, no_sleep):
    db.init_db()
    target = _seed_targets()[0]
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_403):
        fetcher_mod.fetch(
            [target], db_path=tmp_db, navigation_noise=False, max_fetches=10
        )
    with db.get_conn_ctx(tmp_db) as conn:
        rows = conn.execute(
            "SELECT * FROM detection_events WHERE url=?",
            (target["url"],),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["response_code"] == 403


def test_no_detection_event_on_200(tmp_db, monkeypatch, no_sleep):
    db.init_db()
    target = _seed_targets()[0]
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        fetcher_mod.fetch(
            [target], db_path=tmp_db, navigation_noise=False, max_fetches=10
        )
    with db.get_conn_ctx(tmp_db) as conn:
        n = conn.execute("SELECT count(*) FROM detection_events").fetchone()[0]
    assert n == 0


# ---------------------------------------------------------------------------
# ATS fingerprinting on response
# ---------------------------------------------------------------------------

def test_response_carrys_ats_fingerprint(tmp_db, monkeypatch, no_sleep):
    db.init_db()
    target = _seed_targets()[0]   # greenhouse URL
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        responses = fetcher_mod.fetch(
            [target], db_path=tmp_db, navigation_noise=False, max_fetches=10
        )
    assert responses[0].ats_fingerprint == "greenhouse"


# ---------------------------------------------------------------------------
# fetch_log written correctly
# ---------------------------------------------------------------------------

def test_fetch_log_records_each_request(tmp_db, monkeypatch, no_sleep):
    db.init_db()
    targets = _seed_targets()
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        fetcher_mod.fetch(
            targets, db_path=tmp_db, navigation_noise=False, max_fetches=10
        )
    with db.get_conn_ctx(tmp_db) as conn:
        n = conn.execute("SELECT count(*) FROM fetch_log").fetchone()[0]
    assert n == 2


def test_fetch_log_records_last_status(tmp_db, monkeypatch, no_sleep):
    db.init_db()
    target = _seed_targets()[0]
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        fetcher_mod.fetch(
            [target], db_path=tmp_db, navigation_noise=False, max_fetches=10
        )
    with db.get_conn_ctx(tmp_db) as conn:
        row = conn.execute(
            "SELECT last_status FROM fetch_log WHERE url=?",
            (target["url"],),
        ).fetchone()
    assert row["last_status"] == 200
