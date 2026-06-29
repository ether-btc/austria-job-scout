"""CLI tests — exercise the actual argparse pipeline.

These exist because the iter-1 smoke test found a real argparse bug:
--db was on both the parent parser and the subparser-parent, so the
subparser's None default overwrote the user's value. This file pins the
correct behaviour so the bug can't regress.
"""
from __future__ import annotations

import json
import sys

import pytest

from austria_job_scout import cli


def _run(argv: list[str]) -> int:
    """Invoke the CLI with the given argv and return the exit code."""
    return cli.main(argv)


def test_help_exits_zero(capsys):
    """`--help` exits 0 after writing usage to stdout (argparse default)."""
    with pytest.raises(SystemExit) as exc:
        _run(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "austria-job-scout" in captured.out
    # --db is on the subparser-parent so it doesn't appear in top-level --help.
    # It IS visible in each subcommand's --help though:
    for sub in ("ingest", "discover", "fetch"):
        with pytest.raises(SystemExit) as exc:
            _run([sub, "--help"])
        assert exc.value.code == 0
        captured2 = capsys.readouterr()
        assert "--db" in captured2.out, f"{sub} --help missing --db"


def test_db_init_and_stats_with_explicit_db(tmp_db, capsys):
    rc = _run(["db-init", "--db", str(tmp_db)])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["status"] == "ok"
    assert data["schema_version"] == 1
    assert data["db"] == str(tmp_db)

    rc = _run(["db-stats", "--db", str(tmp_db)])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["db_path"] == str(tmp_db)
    # All 16 tables present
    assert len(data["tables"]) >= 16
    assert data["tables"]["wishlist"] == 0
    assert data["tables"]["circuit_breaker"] == 0


def test_db_flag_propagates_via_subparser(tmp_db, capsys):
    """Regression: --db used to be silently dropped because both the parent
    and the subparser-parent had it, and the subparser's None overwrote it."""
    rc = _run(["db-init", "--db", str(tmp_db)])
    assert rc == 0
    capsys.readouterr()

    rc = _run(["ingest", "--role", "Senior Rust Engineer", "--save", "--db", str(tmp_db)])
    assert rc == 0
    capsys.readouterr()

    # Now db-stats on the same DB MUST show the row we just inserted.
    rc = _run(["db-stats", "--db", str(tmp_db)])
    data = json.loads(capsys.readouterr().out)
    assert data["tables"]["reference_jobs"] == 1, (
        "ingest on --db did not land in the right DB — argparse --db regression"
    )


def test_db_flag_falls_back_to_env(tmp_db, monkeypatch, capsys):
    """When --db is omitted, the env var (or default) applies."""
    monkeypatch.setenv("AUSTRIA_JOB_SCOUT_DB", str(tmp_db))
    rc = _run(["db-init"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["db"] == str(tmp_db)


def test_ingest_role_no_db_uses_default(capsys, monkeypatch, tmp_path):
    """Without --db or env, ingest uses the project default DB path."""
    db = tmp_path / "default.db"
    monkeypatch.setenv("AUSTRIA_JOB_SCOUT_DB", str(db))
    # Init first — ingest --save does NOT auto-init (explicit beats implicit).
    rc = _run(["db-init"])
    assert rc == 0
    capsys.readouterr()
    rc = _run(["ingest", "--role", "Senior Rust Engineer", "--save"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["id"] >= 1
    assert data["source"] == "role_name"
    assert "Rust" in data["skills"]


def test_ingest_requires_exactly_one_of_input_or_role(capsys):
    rc = _run(["ingest"])
    assert rc == 2   # argparse usage error
    err = capsys.readouterr().err
    assert "exactly one" in err


def test_ingest_rejects_both_args(capsys):
    rc = _run(["ingest", "--role", "X", "--input", "/tmp/foo.txt"])
    assert rc == 2


def test_new_subcommands_require_args(capsys):
    """Iter-3/4 subcommands are now implemented and require their args."""
    # extract requires --raw (argparse raises SystemExit(2))
    with pytest.raises(SystemExit) as exc_info:
        _run(["extract"])
    assert exc_info.value.code == 2

    # index requires --jobs
    with pytest.raises(SystemExit) as exc_info:
        _run(["index"])
    assert exc_info.value.code == 2

    # score requires --reference
    with pytest.raises(SystemExit) as exc_info:
        _run(["score"])
    assert exc_info.value.code == 2

    # report requires --scored
    with pytest.raises(SystemExit) as exc_info:
        _run(["report"])
    assert exc_info.value.code == 2

    # pipeline requires --input or --role (custom validation, not argparse)
    rc = _run(["pipeline"])
    assert rc == 1


def test_ingest_missing_file_returns_1(tmp_path, capsys):
    rc = _run(["ingest", "--input", str(tmp_path / "does_not_exist.txt")])
    assert rc == 1


def test_discover_no_network_returns_targets(tmp_db, capsys, monkeypatch):
    """discover is network-free by default; must return at least the seeded
    ATS endpoints."""
    monkeypatch.setattr("austria_job_scout.config.AGGRESSIVE_MODE", False)
    rc = _run(["db-init", "--db", str(tmp_db)])
    assert rc == 0
    capsys.readouterr()
    rc = _run(["ingest", "--role", "Senior Rust Engineer", "--save", "--db", str(tmp_db)])
    assert rc == 0
    out = capsys.readouterr().out
    ref = json.loads(out)

    rc = _run(["discover", "--reference", json.dumps(ref), "--db", str(tmp_db)])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["target_count"] > 0
    # All targets must have predicted_relevance, priority, url, ats
    for t in payload["targets"]:
        assert "predicted_relevance" in t
        assert "priority" in t
        assert t["url"].startswith("https://")


def test_discover_writes_to_out_file(tmp_db, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr("austria_job_scout.config.AGGRESSIVE_MODE", False)
    _run(["db-init", "--db", str(tmp_db)])
    capsys.readouterr()
    _run(["ingest", "--role", "Senior Rust Engineer", "--save", "--db", str(tmp_db)])
    ref = json.loads(capsys.readouterr().out)
    out = tmp_path / "targets.json"
    rc = _run(["discover", "--reference", json.dumps(ref),
               "--db", str(tmp_db), "--out", str(out)])
    assert rc == 0
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["target_count"] > 0


def test_fetch_no_navigation_no_targets_returns_zero(tmp_db, capsys, monkeypatch):
    """fetch with empty targets list returns an empty response array."""
    monkeypatch.setattr("austria_job_scout.config.AGGRESSIVE_MODE", False)
    monkeypatch.setattr("austria_job_scout.modules.fetcher._http_get",
                        lambda *a, **kw: ("<html></html>", 200, None))
    monkeypatch.setattr("austria_job_scout.modules.fetcher._sleep_like_human",
                        lambda: None)
    _run(["db-init", "--db", str(tmp_db)])
    capsys.readouterr()
    rc = _run(["fetch", "--targets", "[]", "--db", str(tmp_db)])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["fetched_count"] == 0


def test_fetch_records_in_fetch_log(tmp_db, capsys, monkeypatch):
    """After a successful fetch, fetch_log has one row per unique host."""
    monkeypatch.setattr("austria_job_scout.config.AGGRESSIVE_MODE", False)
    monkeypatch.setattr("austria_job_scout.modules.fetcher._http_get",
                        lambda *a, **kw: ("<html></html>", 200, None))
    monkeypatch.setattr("austria_job_scout.modules.fetcher._sleep_like_human",
                        lambda: None)
    _run(["db-init", "--db", str(tmp_db)])
    capsys.readouterr()
    targets = [{
        "ats": "greenhouse", "source_kind": "ats_board",
        "url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        "company_name": "Acme", "company_domain": "acme.com",
        "predicted_relevance": 0.9, "priority": 10, "notes": "",
    }]
    _run(["fetch", "--targets", json.dumps(targets), "--db", str(tmp_db)])
    capsys.readouterr()
    rc = _run(["db-stats", "--db", str(tmp_db)])
    data = json.loads(capsys.readouterr().out)
    assert data["tables"]["fetch_log"] == 1


def test_db_init_idempotent(tmp_db, capsys):
    rc1 = _run(["db-init", "--db", str(tmp_db)])
    assert rc1 == 0
    capsys.readouterr()
    rc2 = _run(["db-init", "--db", str(tmp_db)])
    assert rc2 == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema_version"] == 1
