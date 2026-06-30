"""Tests for wishlist persistence — overflow targets survive across runs."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from austria_job_scout import config, db
from austria_job_scout.modules import fetcher as fetcher_mod


def _mock_http_ok(url, **_kw):
    return (f"<html>{url}</html>", 200, None)


def _seed_targets(n: int = 3) -> list[dict]:
    return [
        {
            "ats": "greenhouse",
            "source_kind": "ats_board",
            "url": f"https://boards-api.greenhouse.io/v1/boards/acme{i}/jobs",
            "company_name": f"Acme {i}",
            "company_domain": f"acme{i}.com",
            "predicted_relevance": 0.9,
            "priority": 10,
            "notes": "test",
        }
        for i in range(n)
    ]


def _seed_reference_job(db_path) -> int:
    """Insert a reference_job and return its id (wishlist needs a FK target)."""
    import sqlite3
    import time
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            """INSERT INTO reference_jobs
               (created_at, source, source_path, raw_text, title, skills_json)
               VALUES (?, 'role_name', NULL, 'Test role', 'Senior Rust Engineer', '[]')""",
            (int(time.time()),),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def test_wishlist_persists_overflow_targets(tmp_db, monkeypatch, no_sleep):
    """When max_fetches caps the run, remaining targets are persisted to wishlist.
    Works with NULL reference_id (no FK target required)."""
    db.init_db()
    targets = _seed_targets(5)
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        responses = fetcher_mod.fetch(
            targets, db_path=tmp_db, navigation_noise=False, max_fetches=2,
        )
    assert len(responses) == 2
    with db.get_conn_ctx(tmp_db) as conn:
        n = conn.execute(
            "SELECT count(*) FROM wishlist WHERE wishlist_status='pending'"
        ).fetchone()[0]
    assert n == 3


def test_wishlist_persists_on_budget_exhaustion(tmp_db, monkeypatch, no_sleep):
    """When daily budget is exhausted, remaining targets go to wishlist."""
    monkeypatch.setattr(config, "DAILY_BUDGET_RESIDENTIAL", 2)
    db.init_db()
    targets = _seed_targets(5)
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        with pytest.raises(fetcher_mod.DailyBudgetExhausted):
            fetcher_mod.fetch(
                targets, db_path=tmp_db, navigation_noise=False, max_fetches=10,
            )
    with db.get_conn_ctx(tmp_db) as conn:
        n = conn.execute(
            "SELECT count(*) FROM wishlist WHERE wishlist_status='pending'"
        ).fetchone()[0]
    assert n >= 3  # 3 un-fetched targets (budget hit on 3rd, 4th-5th never tried)


def test_load_wishlist_returns_pending_targets(tmp_db, monkeypatch, no_sleep):
    """load_wishlist() returns pending targets sorted by predicted_relevance."""
    db.init_db()
    targets = _seed_targets(3)
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        fetcher_mod.fetch(
            targets, db_path=tmp_db, navigation_noise=False, max_fetches=1,
        )

    loaded = fetcher_mod.load_wishlist(db_path=tmp_db)
    assert len(loaded) == 2
    for t in loaded:
        assert "url" in t
        assert "ats" in t
        assert t.get("from_wishlist") is True


def test_load_wishlist_respects_limit(tmp_db, monkeypatch, no_sleep):
    """load_wishlist(limit=N) returns at most N items."""
    db.init_db()
    targets = _seed_targets(10)
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        fetcher_mod.fetch(
            targets, db_path=tmp_db, navigation_noise=False, max_fetches=1,
        )
    loaded = fetcher_mod.load_wishlist(db_path=tmp_db, limit=3)
    assert len(loaded) == 3


def test_load_wishlist_empty_when_nothing_pending(tmp_db):
    """load_wishlist returns [] when no pending items."""
    db.init_db()
    loaded = fetcher_mod.load_wishlist(db_path=tmp_db)
    assert loaded == []


def test_mark_wishlist_fetched_updates_status(tmp_db, monkeypatch, no_sleep):
    """mark_wishlist_fetched() moves rows to 'fetched' state."""
    db.init_db()
    targets = _seed_targets(3)
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        fetcher_mod.fetch(
            targets, db_path=tmp_db, navigation_noise=False, max_fetches=1,
        )
    loaded = fetcher_mod.load_wishlist(db_path=tmp_db)
    target_url = loaded[0]["url"]
    fetcher_mod.mark_wishlist_fetched(tmp_db, [target_url])

    with db.get_conn_ctx(tmp_db) as conn:
        row = conn.execute(
            "SELECT wishlist_status, fetched_at FROM wishlist WHERE url=?",
            (target_url,),
        ).fetchone()
    assert row["wishlist_status"] == "fetched"
    assert row["fetched_at"] is not None


def test_wishlist_idempotent_on_repeat_persist(tmp_db, monkeypatch, no_sleep):
    """Persisting the same target twice doesn't create duplicate rows."""
    db.init_db()
    targets = _seed_targets(2)
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        fetcher_mod.fetch(
            targets, db_path=tmp_db, navigation_noise=False, max_fetches=1,
        )
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        fetcher_mod.fetch(
            targets, db_path=tmp_db, navigation_noise=False, max_fetches=1,
        )
    with db.get_conn_ctx(tmp_db) as conn:
        n = conn.execute(
            "SELECT count(*) FROM wishlist WHERE url=?", (targets[1]["url"],)
        ).fetchone()[0]
    assert n == 1


def test_wishlist_allows_null_reference_id(tmp_db, monkeypatch, no_sleep):
    """Wishlist accepts targets without a reference_id (NULL FK)."""
    db.init_db()
    targets = _seed_targets(2)
    with patch.object(fetcher_mod, "_http_get", side_effect=_mock_http_ok):
        fetcher_mod.fetch(
            targets, db_path=tmp_db, navigation_noise=False, max_fetches=1,
        )
    with db.get_conn_ctx(tmp_db) as conn:
        n_null = conn.execute(
            "SELECT count(*) FROM wishlist WHERE reference_id IS NULL"
        ).fetchone()[0]
    assert n_null == 1  # the 1 overflowed target has NULL ref_id
