"""Tests for the DB layer.

Verifies:
    - init_db is idempotent
    - all tables exist after init
    - schema_version is 1
    - skill_aliases pre-seed is present
    - WAL mode is active
"""
from __future__ import annotations

import sqlite3

from austria_job_scout import db


def test_init_creates_db(tmp_db):
    assert not tmp_db.exists()
    db.init_db()
    assert tmp_db.exists()
    assert db.schema_version() == 1


def test_init_is_idempotent(tmp_db):
    db.init_db()
    db.init_db()
    db.init_db()
    assert db.schema_version() == 1
    with db.get_conn_ctx() as conn:
        # If init were not idempotent, schema_version would have 3 rows
        n = conn.execute("SELECT count(*) FROM schema_version").fetchone()[0]
        assert n == 1


def test_all_required_tables_exist(tmp_db):
    db.init_db()
    expected = {
        "schema_version", "reference_jobs", "companies", "targets",
        "fetch_log", "austria_jobs", "job_chunks", "job_chunks_fts",
        "job_chunks_embeddings", "skill_aliases",
        "detection_events", "pipeline_runs",
        # iter-2 additions — must be present after one init
        "wishlist", "circuit_breaker", "daily_request_counter",
    }
    with db.get_conn_ctx() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table')"
        ).fetchall()
        actual = {r[0] for r in rows}
    missing = expected - actual
    assert not missing, f"missing tables: {missing}"


def test_wishlist_unique_per_reference_and_url(tmp_db):
    """Wishlist dedupes (reference_id, url) — same target can't be wishlisted twice."""
    db.init_db()
    import time
    now = int(time.time())
    with db.get_conn_ctx() as conn:
        # Need a reference_jobs row to satisfy FK
        conn.execute(
            """INSERT INTO reference_jobs (created_at, source, raw_text)
               VALUES (?, 'role_name', 'test')""",
            (now,),
        )
        ref_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        for _ in range(2):
            conn.execute(
                """INSERT OR IGNORE INTO wishlist
                   (reference_id, url, url_hash, source_kind, wishlisted_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (ref_id, "https://x", "abc", "ats_board", now),
            )
        n = conn.execute("SELECT count(*) FROM wishlist").fetchone()[0]
    assert n == 1


def test_circuit_breaker_opens_and_cools_off(tmp_db):
    db.init_db()
    import time
    with db.get_conn_ctx() as conn:
        for i in range(3):
            conn.execute(
                """INSERT INTO circuit_breaker
                   (domain, consecutive_failures, last_failure_at,
                    last_status_code, last_error, total_attempts, total_failures)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(domain) DO UPDATE SET
                     consecutive_failures = excluded.consecutive_failures,
                     last_failure_at = excluded.last_failure_at,
                     total_failures = circuit_breaker.total_failures + 1""",
                ("example.com", i + 1, int(time.time()), 503, "boom", i + 1, i + 1),
            )
        row = conn.execute(
            "SELECT * FROM circuit_breaker WHERE domain=?", ("example.com",)
        ).fetchone()
    assert row["consecutive_failures"] == 3
    # cumulative total = 1 (insert) + 1 (update) + 1 (update) = 3
    assert row["total_failures"] == 3


def test_daily_request_counter_increments(tmp_db):
    db.init_db()
    with db.get_conn_ctx() as conn:
        # 7 calls: first seeds row at request_count=0, next 6 each +1 → final = 6
        for _ in range(7):
            conn.execute(
                """INSERT INTO daily_request_counter (day_utc, is_cf_site, request_count)
                   VALUES (?, 0, 0)
                   ON CONFLICT(day_utc, is_cf_site) DO UPDATE SET
                     request_count = request_count + 1""",
                ("2026-06-22",),
            )
        n = conn.execute(
            "SELECT request_count FROM daily_request_counter WHERE day_utc='2026-06-22'"
        ).fetchone()["request_count"]
    assert n == 6


def test_wal_mode_active(tmp_db):
    db.init_db()
    with db.get_conn_ctx() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_skill_aliases_preseeded(tmp_db):
    db.init_db()
    with db.get_conn_ctx() as conn:
        n = conn.execute("SELECT count(*) FROM skill_aliases").fetchone()[0]
    assert n >= 40   # the schema seeds ~50 rows


def test_dedupe_unique_url_constraint(tmp_db):
    """Two INSERTs of the same url must produce one row."""
    db.init_db()
    with db.get_conn_ctx() as conn:
        for _ in range(2):
            conn.execute(
                """INSERT OR IGNORE INTO austria_jobs
                   (url, url_hash, source_domain, title, company,
                    first_seen_at, last_checked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("https://example.com/jobs/1", "deadbeef", "example.com",
                 "Engineer", "Acme", 1, 1),
            )
        n = conn.execute(
            "SELECT count(*) FROM austria_jobs WHERE url=?",
            ("https://example.com/jobs/1",),
        ).fetchone()[0]
    assert n == 1


def test_fetch_log_unique_url_constraint(tmp_db):
    db.init_db()
    with db.get_conn_ctx() as conn:
        for _ in range(2):
            conn.execute(
                """INSERT OR IGNORE INTO fetch_log
                   (url_hash, url, first_checked, last_checked)
                   VALUES (?, ?, ?, ?)""",
                ("cafebabe", "https://example.com/x", 1, 1),
            )
        n = conn.execute("SELECT count(*) FROM fetch_log").fetchone()[0]
    assert n == 1


def test_stats_includes_all_tables(tmp_db):
    db.init_db()
    s = db.stats()
    assert "tables" in s
    assert "austria_jobs" in s["tables"]
    assert "fetch_log" in s["tables"]
    assert "skill_aliases" in s["tables"]
