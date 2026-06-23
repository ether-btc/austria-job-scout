"""Tests for the extractor dispatcher and new CLI commands.

Tests the iter-3/4 development:
- extractor.extract() dispatches by ats_fingerprint
- CLI commands extract, index, score, report are wired and functional
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Extractor dispatcher tests
# ---------------------------------------------------------------------------

def test_extractor_returns_empty_for_null_text():
    """extract() returns [] when response has no body."""
    from austria_job_scout.modules.extractor import extract
    from austria_job_scout.modules.fetcher import RawResponse

    resp = RawResponse(url="https://example.com", status_code=200, text=None)
    assert extract(resp) == []


def test_extractor_returns_empty_for_error_status():
    """extract() returns [] when response has error status code."""
    from austria_job_scout.modules.extractor import extract
    from austria_job_scout.modules.fetcher import RawResponse

    resp = RawResponse(url="https://example.com", status_code=403, text="Forbidden")
    assert extract(resp) == []


def test_extractor_dispatches_to_aggregator_for_unknown_ats():
    """extract() dispatches to aggregator extractor for non-ATS fingerprints."""
    from austria_job_scout.modules.extractor import extract
    from austria_job_scout.modules.fetcher import RawResponse

    fake_job = MagicMock(url="https://careers.acme.com", title="Dev")
    with patch("austria_job_scout.modules.extractor._extract_aggregator", return_value=[fake_job]):
        resp = RawResponse(
            url="https://careers.acme.com",
            status_code=200,
            text="<html>jobs</html>",
            ats_fingerprint="unknown",
        )
        result = extract(resp)
        assert len(result) == 1
        assert result[0].title == "Dev"


def test_extractor_dispatches_to_ats_for_greenhouse():
    """extract() dispatches to ATS extractor for greenhouse fingerprint."""
    from austria_job_scout.modules.extractor import extract
    from austria_job_scout.modules.fetcher import RawResponse

    fake_job = MagicMock(url="https://boards.greenhouse.io/acme", title="Engineer")
    with patch("austria_job_scout.modules.extractor._extract_ats", return_value=fake_job):
        resp = RawResponse(
            url="https://boards.greenhouse.io/acme",
            status_code=200,
            text="<html>job</html>",
            ats_fingerprint="greenhouse",
        )
        result = extract(resp)
        assert len(result) == 1
        assert result[0].title == "Engineer"


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------

def _run(argv: list[str]) -> int:
    from austria_job_scout.cli import main
    return main(argv)


def test_cli_index_command(tmp_path, capsys):
    """CLI index command processes a JSON array of jobs."""
    jobs_json = json.dumps([
        {"url": "https://acme.com/1", "title": "Python Dev", "description": "Python developer"},
        {"url": "https://acme.com/2", "title": "Rust Dev", "description": "Rust developer"},
    ])
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(jobs_json)

    rc = _run(["index", "--jobs", str(jobs_file)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["indexed"] == 2
    assert len(out["jobs"]) == 2


def test_cli_score_command(tmp_path, capsys):
    """CLI score command finds matches between reference and candidates."""
    ref_json = json.dumps({"title": "Python Developer", "skills": ["python", "django"]})
    jobs_json = json.dumps([
        {"title": "Senior Python Developer", "skills": ["python", "flask"]},
        {"title": "Rust Engineer", "skills": ["rust", "cargo"]},
    ])
    ref_file = tmp_path / "ref.json"
    ref_file.write_text(ref_json)
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(jobs_json)

    rc = _run(["score", "--reference", str(ref_file), "--jobs", str(jobs_file), "--min-score", "0.0"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, list)
    assert len(out) >= 1


def test_cli_report_command_text_format(capsys):
    """CLI report command generates text report from scored matches."""
    scored_json = json.dumps([
        {"title": "Python Dev", "company": "ACME", "url": "https://acme.com/1", "score": 0.85,
         "breakdown": {"title": 0.8, "description": 0.9, "skills": 0.85}},
    ])
    rc = _run(["report", "--scored", scored_json, "--format", "text"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Python Dev" in out or "ACME" in out


def test_cli_report_command_csv_format(capsys):
    """CLI report command generates CSV report."""
    scored_json = json.dumps([
        {"title": "Dev", "company": "Co", "url": "https://co.com", "score": 0.5,
         "breakdown": {"title": 0.5, "description": 0.5, "skills": 0.5}},
    ])
    rc = _run(["report", "--scored", scored_json, "--format", "csv"])
    assert rc == 0
    out = capsys.readouterr().out
    assert len(out) > 0


def test_cli_report_writes_to_file(tmp_path):
    """CLI report command writes to --out file."""
    scored_json = json.dumps([
        {"title": "Dev", "company": "Co", "url": "https://co.com", "score": 0.5,
         "breakdown": {"title": 0.5, "description": 0.5, "skills": 0.5}},
    ])
    out_path = str(tmp_path / "report.md")
    rc = _run(["report", "--scored", scored_json, "--format", "text", "--out", out_path])
    assert rc == 0
    assert Path(out_path).exists()
    content = Path(out_path).read_text()
    assert len(content) > 0


def test_cli_extract_command_with_empty_raw(capsys):
    """CLI extract command handles empty response list."""
    rc = _run(["extract", "--raw", "[]"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == []


def test_cli_extract_command_processes_response(tmp_path, capsys):
    """CLI extract command processes a response and extracts jobs."""
    raw_json = json.dumps([
        {"url": "https://careers.acme.com", "status_code": 200,
         "text": "<html>No jobs here</html>", "ats_fingerprint": "unknown"},
    ])
    rc = _run(["extract", "--raw", raw_json])
    assert rc == 0
    # Should produce a JSON array (may be empty if extraction yields nothing)
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, list)


# ---------------------------------------------------------------------------
# Pipeline import + API tests
# ---------------------------------------------------------------------------

def test_pipeline_imports_cleanly():
    """Pipeline module imports without errors."""
    from austria_job_scout.modules.pipeline import JobScoutPipeline, run_pipeline
    assert JobScoutPipeline is not None
    assert run_pipeline is not None


def test_pipeline_init_creates_indexer():
    """Pipeline.__init__ creates a JobIndexer."""
    from austria_job_scout.modules.pipeline import JobScoutPipeline
    pipeline = JobScoutPipeline(use_ml=False)
    assert pipeline.indexer is not None
    assert pipeline.use_ml is False
