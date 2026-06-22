"""SQLite singleton + migration runner.

The DB is opened in WAL mode for concurrent readers + single writer. All
schema setup is idempotent — calling :func:`init_db` on an already-initialized
DB is a no-op (returns instantly).

Every module that needs the DB should call :func:`get_conn` (preferred for
short-lived transactions) or :func:`get_conn_ctx` (context manager).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _connect(path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with project defaults."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        detect_types=sqlite3.PARSE_DECLTYPES,
        isolation_level=None,  # autocommit; we manage txns explicitly
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # Performance pragmas — safe for our read-mostly workload.
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def get_conn(path: Path | None = None) -> sqlite3.Connection:
    """Return a fresh connection. Caller is responsible for closing it."""
    return _connect(path or config.db_path())


@contextmanager
def get_conn_ctx(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Context-managed connection with auto-close + auto-commit semantics.

    The connection is in autocommit mode; this context manager only handles
    RAII/cleanup. Wrap multi-statement writes in ``with conn:`` blocks.
    """
    conn = get_conn(path)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema setup
# ---------------------------------------------------------------------------

def init_db(path: Path | None = None) -> None:
    """Apply the schema file to the given DB path. Idempotent."""
    schema = Path(config.SCHEMA_PATH).read_text(encoding="utf-8")
    with get_conn_ctx(path) as conn:
        conn.executescript(schema)


def schema_version(path: Path | None = None) -> int:
    """Return the current schema version, or 0 if uninitialized."""
    try:
        with get_conn_ctx(path) as conn:
            row = conn.execute("SELECT max(version) AS v FROM schema_version").fetchone()
            return int(row["v"] or 0)
    except sqlite3.OperationalError:
        return 0


# ---------------------------------------------------------------------------
# Stats helpers (used by `db-stats` subcommand and tests)
# ---------------------------------------------------------------------------

def stats(path: Path | None = None) -> dict:
    """Return row counts + DB size for every table. Cheap to call."""
    with get_conn_ctx(path) as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        out = {"tables": {}, "db_path": str(path or config.db_path())}
        for t in tables:
            try:
                n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                out["tables"][t] = n
            except sqlite3.OperationalError:
                out["tables"][t] = "ERR"
    try:
        out["db_size_bytes"] = (path or config.db_path()).stat().st_size
    except FileNotFoundError:
        out["db_size_bytes"] = 0
    return out
