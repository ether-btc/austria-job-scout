"""Tests for Phase 6.1 `discover-kmu` — scout CSV → Target dicts.

The package owns the contract that was previously living as a scratch
script in /srv/sync/company-recheck-2026-07/scripts/kmu_career_urls.py.
These tests pin the invariants so the wire-up can't regress.

Invariants covered:
    1. Sentinel domains (``nan``, ``none``, ``null``, ``""``, TLDless) are
       filtered BEFORE URL expansion (would otherwise produce
       ``https://jobs.nan/`` and burn the residential fetch budget).
    2. Each non-sentinel row produces the documented 16 candidate URLs via
       :func:`build_kmu_career_urls`.
    3. Output dicts have the same keys as :func:`target_discovery.discover`'s
       output, so they pipe cleanly into ``fetch``.
    4. Dedupe-by-URL drops duplicates across sheets.
    5. ``max_rows`` truncation respects sheet priority (wishlist first).
    6. Directory mode auto-discovers the three known scout sheets.
    7. The CLI subcommand `discover-kmu` round-trips a real-shape CSV.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

from austria_job_scout import cli
from austria_job_scout.modules.kmu_wien_discovery import (
    build_kmu_career_urls,
    scout_csv_targets,
)


# Canonical CSV header shape — mirrors what company-quickcheck day-1 emits.
SCOUT_COLUMNS = (
    "row_id", "name", "company_website", "sector", "registry_status",
)


def _write_scout(path: Path, rows: list[dict]) -> None:
    """Write a list of dict rows as a scout_*.csv with the canonical header."""
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SCOUT_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({col: r.get(col, "") for col in SCOUT_COLUMNS})


# ---------------------------------------------------------------------------
# Unit tests for scout_csv_targets()
# ---------------------------------------------------------------------------


def test_scout_csv_targets_filters_sentinel_domains(tmp_path: Path):
    """Sentinel domains must produce ZERO candidates — the entire point.

    Without this filter, ``https://jobs.nan/`` would be emitted and the
    residential fetch budget would burn on garbage URLs.
    """
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "Real Co",  "company_website": "https://realco.at"},
        {"row_id": "2", "name": "Nan Co",   "company_website": "nan"},
        {"row_id": "3", "name": "None Co",  "company_website": "none"},
        {"row_id": "4", "name": "Null Co",  "company_website": "null"},
        {"row_id": "5", "name": "Empty Co", "company_website": ""},
        {"row_id": "6", "name": "TLDless Co", "company_website": "nodot"},
        {"row_id": "7", "name": "Scheme Nan", "company_website": "https://nan"},
        {"row_id": "8", "name": "WWW Real", "company_website": "https://www.realco.at"},
    ])

    targets = scout_csv_targets(csv_path)

    # Real Co and WWW Real are the SAME apex → 16 URLs after dedupe.
    assert len(targets) == 16, f"expected 16 candidates, got {len(targets)}"

    # Sanity: no sentinel host survived.
    from urllib.parse import urlparse
    hosts = {urlparse(t["url"]).hostname for t in targets}
    for sentinel in ("nan", "none", "null", "nodot", ""):
        assert sentinel not in hosts, (
            f"sentinel host {sentinel!r} leaked through: {sorted(hosts)}"
        )
    assert "realco.at" in hosts


def test_scout_csv_targets_filters_malformed_csv_urls(tmp_path: Path):
    """Defence in depth: CSV typos with commas/semicolons in the website
    field must not produce malformed candidate URLs (Pillar 0 — never
    burn residential budget on a guaranteed-fail request).

    Regression: real-world scout CSV had ``https://irm.at, www.olf.com``
    as a website value; the old normaliser took the comma as part of the
    host, producing ``https://jobs.irm.at, www.olf.com/karriere`` which
    would never resolve.

    Note: trailing whitespace alone is benign (we strip it). Internal
    whitespace or separator characters (`,;|`) are the real signal that
    two URLs ended up in one cell.
    """
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "Good",    "company_website": "https://good.at"},
        {"row_id": "2", "name": "CSV Typo Comma", "company_website": "https://irm.at, www.olf.com"},
        {"row_id": "3", "name": "CSV Typo Semi",  "company_website": "irm.at;"},
        {"row_id": "4", "name": "CSV Typo Pipe",  "company_website": "foo|bar.at"},
    ])

    targets = scout_csv_targets(csv_path)

    # Only "Good" survives — the others all have separator characters.
    assert len(targets) == 16, f"expected 16 (good only), got {len(targets)}"
    from urllib.parse import urlparse
    for t in targets:
        host = urlparse(t["url"]).hostname or ""
        for bad in (" ", "\t", ",", ";", "|"):
            assert bad not in host, (
                f"malformed host leaked: {host!r} from {t['url']}"
            )
        # All surviving targets belong to the "Good" company
        assert t["company_name"] == "Good"


def test_scout_csv_targets_handles_leading_dot_edge_case(tmp_path: Path):
    """A stray leading dot in the host (CSV edge case ``.foo.at``) is
    normalised to ``foo.at``. Multiple leading dots are stripped too.
    But a host that is *only* dots, or one with a trailing dot, must be
    rejected entirely.
    """
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "Leading Dot",   "company_website": "https://.good.at"},
        {"row_id": "2", "name": "Double Dots",   "company_website": "..good.at"},
        {"row_id": "3", "name": "Trailing Dot",  "company_website": "bad.at."},
    ])

    targets = scout_csv_targets(csv_path)

    # Only "Leading Dot" and "Double Dots" survive (both normalise to
    # good.at, so 16 candidates after dedupe). "Trailing Dot" is rejected.
    assert len(targets) == 16, f"expected 16, got {len(targets)}"
    from urllib.parse import urlparse
    for t in targets:
        host = urlparse(t["url"]).hostname or ""
        # No leading or trailing dots on the bare host
        bare = host.split(".", 1)[-1] if "." in host else host
        assert not host.startswith(".")
        assert not host.endswith(".")
    # The "Trailing Dot" company should not appear
    names = {t["company_name"] for t in targets}
    assert "Trailing Dot" not in names


def test_scout_csv_targets_tolerates_trailing_whitespace(tmp_path: Path):
    """Trailing whitespace in the website cell is harmless — we strip it.
    Internal whitespace or separators, on the other hand, indicate a
    multi-URL cell that must be rejected.
    """
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "Trailing Space", "company_website": "good.at   "},
        {"row_id": "2", "name": "Trailing Newline", "company_website": "good.at\n"},
    ])

    targets = scout_csv_targets(csv_path)
    # Both rows resolve to the same apex `good.at` → 16 candidates after dedupe.
    assert len(targets) == 16
    from urllib.parse import urlparse
    for t in targets:
        host = urlparse(t["url"]).hostname or ""
        assert not any(c in host for c in " ,\t\n;|")


def test_scout_csv_targets_emits_target_shape(tmp_path: Path):
    """Output dicts must have exactly the keys `discover` emits.

    This is what makes `discover-kmu | fetch` work without translation.
    """
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "NETAVIS Software GmbH", "company_website": "https://netavis.net"},
    ])
    targets = scout_csv_targets(csv_path)

    expected_keys = {
        "ats", "source_kind", "url", "company_name", "company_domain",
        "predicted_relevance", "priority", "notes",
    }
    assert len(targets) == 16
    for t in targets:
        assert expected_keys.issubset(t.keys()), f"missing keys: {expected_keys - t.keys()}"
        assert t["source_kind"] == "kmu_career_url"
        assert t["company_name"] == "NETAVIS Software GmbH"
        assert t["company_domain"] == "netavis.net"
        assert 0.0 <= t["predicted_relevance"] <= 1.0
        assert isinstance(t["priority"], int)
        assert t["notes"].startswith("kmu_wishlist:NETAVIS Software GmbH")
        # Row id and sheet name should appear in notes for traceability.
        assert "row=1" in t["notes"]
        assert "sheet=scout_review_required.csv" in t["notes"]


def test_scout_csv_targets_dedupes_across_sheets(tmp_path: Path):
    """Same domain across two sheets → 16 candidates, not 32."""
    review = tmp_path / "scout_review_required.csv"
    registry = tmp_path / "scout_registry_open.csv"
    _write_scout(review, [
        {"row_id": "1", "name": "ARAX", "company_website": "https://ara.at"},
    ])
    _write_scout(registry, [
        {"row_id": "99", "name": "ARAX Recycling", "company_website": "ara.at"},
    ])

    targets = scout_csv_targets(tmp_path)
    assert len(targets) == 16


def test_scout_csv_targets_respects_sheet_priority_for_truncation(tmp_path: Path):
    """max_rows should consume scout_review_required first (priority 1)."""
    review = tmp_path / "scout_review_required.csv"
    registry = tmp_path / "scout_registry_open.csv"
    _write_scout(review, [
        {"row_id": "1", "name": "Wishlist Co A", "company_website": "https://wl-a.at"},
        {"row_id": "2", "name": "Wishlist Co B", "company_website": "https://wl-b.at"},
    ])
    _write_scout(registry, [
        {"row_id": "10", "name": "Registry Co X", "company_website": "https://reg-x.at"},
        {"row_id": "11", "name": "Registry Co Y", "company_website": "https://reg-y.at"},
    ])

    targets = scout_csv_targets(tmp_path, max_rows=2)
    # 2 rows × 16 URLs = 32 candidates from the wishlist only.
    assert len(targets) == 32
    for t in targets:
        assert "scout_review_required.csv" in t["notes"]


def test_scout_csv_targets_directory_mode_auto_discovers_sheets(tmp_path: Path):
    """Directory mode picks up all three known scout sheets."""
    review = tmp_path / "scout_review_required.csv"
    registry = tmp_path / "scout_registry_open.csv"
    verified = tmp_path / "scout_strict_verified.csv"
    _write_scout(review, [
        {"row_id": "1", "name": "WL",  "company_website": "https://wl.at"},
    ])
    _write_scout(registry, [
        {"row_id": "2", "name": "RG",  "company_website": "https://rg.at"},
    ])
    _write_scout(verified, [
        {"row_id": "3", "name": "VF",  "company_website": "https://vf.at"},
    ])

    targets = scout_csv_targets(tmp_path)
    # 3 distinct apex domains × 16 URLs each = 48 candidates.
    assert len(targets) == 48
    sheets_seen = {t["notes"].split("sheet=")[1].split(";")[0] for t in targets}
    assert sheets_seen == {
        "scout_review_required.csv",
        "scout_registry_open.csv",
        "scout_strict_verified.csv",
    }


def test_scout_csv_targets_primary_alternate_relevance(tmp_path: Path):
    """First URL per domain uses primary_relevance, rest use alternate_relevance."""
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "Acme", "company_website": "https://acme.at"},
    ])

    targets = scout_csv_targets(
        csv_path,
        primary_relevance=0.9,
        alternate_relevance=0.1,
    )
    assert len(targets) == 16
    primary = [t for t in targets if t["predicted_relevance"] == 0.9]
    alternate = [t for t in targets if t["predicted_relevance"] == 0.1]
    assert len(primary) == 1, "exactly one primary per domain"
    assert len(alternate) == 15


def test_scout_csv_targets_missing_sheet_is_skipped(tmp_path: Path, caplog):
    """A missing optional sheet should be skipped silently (debug log only)."""
    targets = scout_csv_targets(tmp_path)  # empty dir
    assert targets == []


def test_scout_csv_targets_handles_website_alias(tmp_path: Path):
    """Some scout rows may use 'website' instead of 'company_website'."""
    csv_path = tmp_path / "scout_review_required.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=("row_id", "name", "website"))
        w.writeheader()
        w.writerow({"row_id": "1", "name": "Alt Header Co", "website": "https://alt.at"})

    targets = scout_csv_targets(csv_path)
    assert len(targets) == 16
    assert all(t["company_domain"] == "alt.at" for t in targets)


def test_scout_csv_targets_skips_rows_without_name(tmp_path: Path):
    """Rows missing both 'name' and 'company_name' must be skipped."""
    csv_path = tmp_path / "scout_review_required.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=("row_id", "company_website"))
        w.writeheader()
        w.writerow({"row_id": "1", "company_website": "https://noname.at"})

    targets = scout_csv_targets(csv_path)
    assert targets == []


# ---------------------------------------------------------------------------
# CLI subcommand tests
# ---------------------------------------------------------------------------


def _run_cli(argv: list[str]) -> int:
    return cli.main(argv)


def test_cli_discover_kmu_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        _run_cli(["discover-kmu", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--scout-csv" in out
    assert "--max-rows" in out
    # argparse wraps long descriptions; just check for a stable substring.
    assert "discover-kmu" in out
    assert "scout" in out.lower()


def test_cli_discover_kmu_missing_path_exits_nonzero(tmp_path, capsys):
    rc = _run_cli([
        "discover-kmu",
        "--scout-csv", str(tmp_path / "does-not-exist.csv"),
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "does not exist" in err


def test_cli_discover_kmu_round_trips_real_shape(tmp_path, capsys):
    """Full CLI round-trip: CSV in → JSON out → matches fetch schema."""
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "NETAVIS", "company_website": "https://netavis.net"},
        {"row_id": "2", "name": "Nan Co", "company_website": "nan"},
    ])
    out_path = tmp_path / "targets.json"

    rc = _run_cli([
        "discover-kmu",
        "--scout-csv", str(csv_path),
        "--out", str(out_path),
    ])
    assert rc == 0

    payload = json.loads(out_path.read_text())
    assert payload["target_count"] == 16
    assert payload["scout_csv"] == str(csv_path)
    assert "targets" in payload
    # Every target must have exactly the keys the fetcher reads.
    required = {"url", "company_name", "company_domain", "predicted_relevance",
                "priority", "ats", "source_kind"}
    for t in payload["targets"]:
        assert required.issubset(t.keys())


def test_cli_discover_kmu_min_relevance_filter(tmp_path, capsys):
    """--min-relevance drops targets; only the primary candidate survives
    a high threshold."""
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "Acme", "company_website": "https://acme.at"},
    ])

    rc = _run_cli([
        "discover-kmu",
        "--scout-csv", str(csv_path),
        "--primary-relevance", "0.9",
        "--alternate-relevance", "0.1",
        "--min-relevance", "0.5",
        "--out", str(tmp_path / "targets.json"),
    ])
    assert rc == 0
    payload = json.loads((tmp_path / "targets.json").read_text())
    # Only the 1 primary candidate has relevance >= 0.5.
    assert payload["target_count"] == 1


def test_cli_discover_kmu_max_targets_cap(tmp_path, capsys):
    """--max-targets hard-caps the final list."""
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "A", "company_website": "https://a.at"},
        {"row_id": "2", "name": "B", "company_website": "https://b.at"},
    ])
    # 2 domains × 16 = 32 candidates; cap at 5.
    rc = _run_cli([
        "discover-kmu",
        "--scout-csv", str(csv_path),
        "--max-targets", "5",
        "--out", str(tmp_path / "targets.json"),
    ])
    assert rc == 0
    payload = json.loads((tmp_path / "targets.json").read_text())
    assert payload["target_count"] == 5


def test_cli_discover_kmu_directory_input(tmp_path, capsys):
    """Passing a directory scans all known scout_*.csv sheets."""
    review = tmp_path / "scout_review_required.csv"
    registry = tmp_path / "scout_registry_open.csv"
    _write_scout(review, [
        {"row_id": "1", "name": "WL",  "company_website": "https://wl.at"},
    ])
    _write_scout(registry, [
        {"row_id": "2", "name": "RG",  "company_website": "https://rg.at"},
    ])

    rc = _run_cli([
        "discover-kmu",
        "--scout-csv", str(tmp_path),
        "--out", str(tmp_path / "targets.json"),
    ])
    assert rc == 0
    payload = json.loads((tmp_path / "targets.json").read_text())
    assert payload["scout_csv_kind"] == "directory"
    # 2 distinct apex domains × 16 = 32.
    assert payload["target_count"] == 32


def test_cli_discover_kmu_output_is_pure_json(capsys, tmp_path):
    """Without --out, stdout must be valid JSON (no log lines mixed in)."""
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "X", "company_website": "https://x.at"},
    ])
    rc = _run_cli(["discover-kmu", "--scout-csv", str(csv_path)])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # must parse
    assert payload["target_count"] == 16
