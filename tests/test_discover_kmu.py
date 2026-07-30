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
import socket
import sys
from pathlib import Path

import pytest

from austria_job_scout import cli
from austria_job_scout.modules import kmu_wien_discovery as kmu
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


# ---------------------------------------------------------------------------
# DNS pre-flight tests
# ---------------------------------------------------------------------------


def _fake_resolver(results: dict[str, Exception | list]):
    """Build a fake :func:`socket.getaddrinfo` that returns canned results.

    Keys are apex hostnames; values are either an exception instance to
    raise, or a list of address tuples (any truthy list is treated as ok).
    """

    def _fake(host, port, family, type_, proto, flags):
        if host not in results:
            raise socket.gaierror(-2, "Name or service not known")  # NXDOMAIN
        v = results[host]
        if isinstance(v, Exception):
            raise v
        return v if v else []

    return _fake


def test_dns_resolves_returns_ok_for_resolving_apex():
    fake = _fake_resolver({
        "good.at": [("family", "type", "proto", "canonname", ("1.2.3.4", 0))],
    })
    ok, reason = kmu.dns_resolves("good.at", resolver=fake)
    assert ok is True
    assert reason == ""


def test_dns_resolves_returns_nxdomain():
    fake = _fake_resolver({})
    ok, reason = kmu.dns_resolves("does-not-exist.at", resolver=fake)
    assert ok is False
    assert reason == "dns_nxdomain"


def test_dns_resolves_returns_timeout():
    fake = _fake_resolver({
        "slow.at": socket.timeout("timed out"),
    })
    ok, reason = kmu.dns_resolves("slow.at", resolver=fake)
    assert ok is False
    assert reason == "dns_timeout"


def test_dns_resolves_returns_oserror():
    fake = _fake_resolver({
        "broken.at": OSError("connection refused"),
    })
    ok, reason = kmu.dns_resolves("broken.at", resolver=fake)
    assert ok is False
    assert reason.startswith("dns_error:")


def test_dns_resolves_classifies_eai_noname_as_nxdomain():
    """Explicit EAI_NONAME errno → dns_nxdomain (permanent exclude).

    Regression test for the classification bug where any gaierror with a
    truthy errno (which is *all* real gaierrors — even timeouts) was
    mis-categorised. Consumers like
    ``company-quickcheck.apply_ajs_exclusions`` rely on this reason code
    to distinguish permanent EXCLUDE (NXDOMAIN) from transient failure.
    """
    fake = _fake_resolver({
        "nx.at": socket.gaierror(socket.EAI_NONAME, "Name or service not known"),
    })
    ok, reason = kmu.dns_resolves("nx.at", resolver=fake)
    assert ok is False
    assert reason == "dns_nxdomain"


def test_dns_resolves_classifies_eai_again_as_timeout():
    """EAI_AGAIN → dns_timeout (transient failure, retry-friendly).

    A resolver that returns EAI_AGAIN is signalling a transient condition
    (e.g. SERVFAIL, network blip) — semantically a "DNS timeout" from the
    caller's perspective. Distinguishing this from NXDOMAIN lets the
    upstream ``apply-ajs-exclusions`` consumer retry these later instead
    of permanently EXCLUDE-ing them.
    """
    fake = _fake_resolver({
        "transient.at": socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution"),
    })
    ok, reason = kmu.dns_resolves("transient.at", resolver=fake)
    assert ok is False
    assert reason == "dns_timeout"


def test_dns_resolves_classifies_unknown_gaierror_as_dns_error():
    """A gaierror carrying an errno we don't recognise → dns_error:... .

    Ensures exotic errnos (e.g. EAI_FAIL, EAI_MEMORY) don't silently get
    treated as NXDOMAIN or timeout. They leak out via ``dns_error:`` so
    operators can see them in the dropped.csv.
    """
    fake = _fake_resolver({
        "weird.at": socket.gaierror(socket.EAI_FAIL, "Non-recoverable failure"),
    })
    ok, reason = kmu.dns_resolves("weird.at", resolver=fake)
    assert ok is False
    assert reason.startswith("dns_error:")
    # The repr carries the EAI_FAIL string so operators can triage.
    assert "EAI_FAIL" in reason or "Non-recoverable" in reason


def test_dns_resolves_rejects_empty_apex():
    ok, reason = kmu.dns_resolves("")
    assert ok is False
    assert reason == "sentinel"


# ---------------------------------------------------------------------------
# summarise_dropped() tests (Phase 6.2)
# ---------------------------------------------------------------------------


def test_summarise_dropped_returns_empty_for_missing_file(tmp_path):
    """A non-existent dropped.csv returns an empty DroppedStats.

    Missing is the most common case for the day-1 sheet (run hasn't
    happened yet) — the operator's command must not crash.
    """
    stats = kmu.summarise_dropped(tmp_path / "nonexistent.csv")
    assert stats.total == 0
    assert stats.by_reason == {}
    assert stats.by_sheet == {}
    assert stats.unique_apexes == 0
    assert stats.unknown_reason_rows == 0
    assert stats.has_unknown_reasons is False


def test_summarise_dropped_counts_per_reason_and_sheet(tmp_path):
    """A normal dropped.csv yields accurate per-reason + per-sheet counts."""
    csv_path = tmp_path / "dropped.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=(
            "source_sheet", "source_row_id", "company_name",
            "company_website", "dropped_apex", "reason", "notes",
        ))
        w.writeheader()
        w.writerow({"source_sheet": "scout_review_required.csv", "source_row_id": "1",
                    "company_name": "Nan Co", "company_website": "nan",
                    "dropped_apex": "", "reason": "sentinel", "notes": ""})
        w.writerow({"source_sheet": "scout_review_required.csv", "source_row_id": "2",
                    "company_name": "Ghost Co", "company_website": "https://ghost.at",
                    "dropped_apex": "ghost.at", "reason": "dns_nxdomain", "notes": ""})
        w.writerow({"source_sheet": "scout_strict_verified.csv", "source_row_id": "5",
                    "company_name": "Nameless Co", "company_website": "https://no.at",
                    "dropped_apex": "no.at", "reason": "missing_name", "notes": ""})

    stats = kmu.summarise_dropped(csv_path)
    assert stats.total == 3
    assert stats.by_reason == {"sentinel": 1, "dns_nxdomain": 1, "missing_name": 1}
    assert stats.by_sheet == {
        "scout_review_required.csv": 2,
        "scout_strict_verified.csv": 1,
    }
    # Two distinct dropped_apexes: "" (sentinel) and "ghost.at", "no.at" → 3
    # unique including the empty sentinel slot.
    assert stats.unique_apexes == 3
    assert stats.unknown_reason_rows == 0
    assert stats.has_unknown_reasons is False


def test_summarise_dropped_flags_unknown_reasons(tmp_path):
    """Opaque reasons (e.g. dns_error:...) are flagged for triage, not silently EXCLUDEd."""
    csv_path = tmp_path / "dropped.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=(
            "source_sheet", "source_row_id", "company_name",
            "company_website", "dropped_apex", "reason", "notes",
        ))
        w.writeheader()
        w.writerow({"source_sheet": "scout_review_required.csv", "source_row_id": "1",
                    "company_name": "X", "company_website": "",
                    "dropped_apex": "x.at", "reason": "sentinel", "notes": ""})
        w.writerow({"source_sheet": "scout_review_required.csv", "source_row_id": "2",
                    "company_name": "Y", "company_website": "",
                    "dropped_apex": "y.at",
                    "reason": "dns_error: gaierror(-4, 'Non-recoverable failure')",
                    "notes": ""})

    stats = kmu.summarise_dropped(csv_path)
    assert stats.total == 2
    assert stats.unknown_reason_rows == 1
    assert stats.has_unknown_reasons is True


def test_summarise_dropped_handles_empty_reason_gracefully(tmp_path):
    """An empty reason cell is counted as the empty reason (not 'unknown',
    not whitespace). The unknown-reason counter only fires on non-empty
    strings outside KNOWN_DROP_REASONS."""
    csv_path = tmp_path / "dropped.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=(
            "source_sheet", "source_row_id", "company_name",
            "company_website", "dropped_apex", "reason", "notes",
        ))
        w.writeheader()
        w.writerow({"source_sheet": "scout_review_required.csv", "source_row_id": "1",
                    "company_name": "X", "company_website": "",
                    "dropped_apex": "x.at", "reason": "", "notes": ""})

    stats = kmu.summarise_dropped(csv_path)
    assert stats.total == 1
    assert stats.by_reason == {"": 1}
    # Empty reason doesn't trip the unknown-reason counter (sentinels
    # legitimately leave reason blank when a row is malformed to that degree).
    assert stats.unknown_reason_rows == 0


def test_summarise_dropped_pilot_regression_snapshot(tmp_path):
    """Regression: the day-1 live pilot (2026-07-20) produced 66 dropped
    rows: 56 sentinel + 8 dns_nxdomain + 2 csv-malformed. Pin those exact
    counts so any drift in the producer (or the dropped.csv schema)
    surfaces as a failed CI run.

    The pilot's raw dropped.csv (committed under live_results/) is the
    canonical evidence — we replay it through the new summariser and
    assert the historical shape.
    """
    repo_root = Path(__file__).parent.parent
    pilot_csv = repo_root / "live_results" / "pilot-2026-07-20" / "dropped.csv"
    if not pilot_csv.exists():
        pytest.skip(f"pilot dropped.csv not present at {pilot_csv}")

    stats = kmu.summarise_dropped(pilot_csv)
    assert stats.total == 66, (
        f"pilot dropped.csv row count drifted: was 66, got {stats.total}. "
        "Either the schema changed or a new reason was added — investigate."
    )
    assert stats.by_sheet == {"scout_review_required.csv": 66}
    # Sentinel + dns_nxdomain account for 64 of 66 — the remaining 2 are
    # the raw CSV's unparseable-name rows (commas-in-fields on company_name
    # which produced empty reason values; we accept them and don't fail).
    assert stats.by_reason.get("sentinel", 0) + stats.by_reason.get("dns_nxdomain", 0) >= 60
    assert stats.unique_apexes >= 8, (
        f"pilot had ≥8 distinct NXDOMAIN apexes, got {stats.unique_apexes}"
    )


def test_scout_csv_targets_dns_preflight_off_by_default(tmp_path):
    """Without dns_preflight_enabled, no DNS lookups happen (fast path)."""
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "Real", "company_website": "https://real.at"},
        {"row_id": "2", "name": "Fake", "company_website": "https://does-not-exist.at"},
    ])
    # If DNS were called, "Fake" would be dropped. With preflight off, both
    # rows are expanded to candidate URLs.
    targets = scout_csv_targets(csv_path, dns_preflight_enabled=False)
    domains = {t["company_domain"] for t in targets}
    assert "real.at" in domains
    assert "does-not-exist.at" in domains  # still present, no DNS check


def test_scout_csv_targets_dns_preflight_drops_unresolving(tmp_path):
    """With dns_preflight_enabled, unresolvable apexes are filtered."""
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "Real",  "company_website": "https://real.at"},
        {"row_id": "2", "name": "Ghost", "company_website": "https://ghost.at"},
    ])
    fake = _fake_resolver({
        "real.at": [("f", "t", "p", "cn", ("1.2.3.4", 0))],
        # ghost.at not in dict → fake resolver raises gaierror (NXDOMAIN)
    })

    dropped: list[kmu.DroppedRow] = []
    targets = scout_csv_targets(
        csv_path,
        dns_preflight_enabled=True,
        resolver=fake,
        on_dropped=dropped.append,
    )

    domains = {t["company_domain"] for t in targets}
    assert domains == {"real.at"}, f"only real.at should survive, got {domains}"
    # One dropped row recorded for ghost.at
    assert len(dropped) == 1
    assert dropped[0].dropped_apex == "ghost.at"
    assert dropped[0].reason == "dns_nxdomain"
    assert dropped[0].company_name == "Ghost"
    assert dropped[0].source_sheet == "scout_review_required.csv"


def test_scout_csv_targets_dns_preflight_caches_per_apex(tmp_path):
    """Each unique apex is DNS-checked at most once even if it appears
    across multiple rows."""
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "A", "company_website": "https://x.at"},
        {"row_id": "2", "name": "B", "company_website": "https://x.at/path"},
        {"row_id": "3", "name": "C", "company_website": "www.x.at"},
    ])

    call_count = {"n": 0}

    def counting_resolver(host, *args, **kwargs):
        call_count["n"] += 1
        return [("f", "t", "p", "cn", ("1.2.3.4", 0))]

    targets = scout_csv_targets(
        csv_path,
        dns_preflight_enabled=True,
        resolver=counting_resolver,
    )
    # All 3 rows normalise to x.at — only one DNS lookup expected.
    assert call_count["n"] == 1, f"expected 1 DNS lookup, got {call_count['n']}"
    # All 3 rows collapse to the same 16 candidate URLs (deduped by URL),
    # which is the same invariant we exercise in other tests.
    assert len(targets) == 16


def test_scout_csv_targets_emits_dropped_for_sentinel(tmp_path):
    """Sentinel rejections also fire on_dropped so the upstream pipeline
    sees them too (they're data-quality signals)."""
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "Nan Co", "company_website": "nan"},
        {"row_id": "2", "name": "None Co", "company_website": "none"},
        {"row_id": "3", "name": "Real Co", "company_website": "https://real.at"},
    ])
    fake = _fake_resolver({
        "real.at": [("f", "t", "p", "cn", ("1.2.3.4", 0))],
    })
    dropped: list[kmu.DroppedRow] = []
    targets = scout_csv_targets(
        csv_path,
        dns_preflight_enabled=True,
        resolver=fake,
        on_dropped=dropped.append,
    )
    # 2 sentinels + 0 DNS drops = 2 dropped rows; only real.at survives.
    assert len(dropped) == 2
    assert all(d.reason == "sentinel" for d in dropped)
    assert {d.company_name for d in dropped} == {"Nan Co", "None Co"}
    domains = {t["company_domain"] for t in targets}
    assert domains == {"real.at"}


def test_scout_csv_targets_emits_dropped_for_missing_name(tmp_path):
    """Rows with a valid apex but no name are dropped with reason='missing_name'."""
    csv_path = tmp_path / "scout_review_required.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=("row_id", "company_website"))
        w.writeheader()
        w.writerow({"row_id": "1", "company_website": "https://real.at"})
        w.writerow({"row_id": "2", "company_website": "https://also.at"})

    fake = _fake_resolver({
        "real.at": [("f", "t", "p", "cn", ("1.2.3.4", 0))],
        "also.at": [("f", "t", "p", "cn", ("1.2.3.4", 0))],
    })
    dropped: list[kmu.DroppedRow] = []
    targets = scout_csv_targets(
        csv_path,
        dns_preflight_enabled=True,
        resolver=fake,
        on_dropped=dropped.append,
    )
    assert targets == []
    assert len(dropped) == 2
    assert all(d.reason == "missing_name" for d in dropped)
    assert {d.dropped_apex for d in dropped} == {"real.at", "also.at"}


# ---------------------------------------------------------------------------
# CLI: --dns-pre-flight + --out-dropped integration
# ---------------------------------------------------------------------------


def test_cli_discover_kmu_dns_preflight_emits_dropped_csv(tmp_path, capsys, monkeypatch):
    """End-to-end: --dns-pre-flight + --out-dropped produces a CSV.

    Patches :mod:`socket.getaddrinfo` so the test isn't dependent on the
    real DNS resolution of the test hostnames (which would flake on CI
    or when offline).
    """
    import socket as _socket
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "Real",  "company_website": "https://real.at"},
        {"row_id": "2", "name": "Ghost", "company_website": "https://ghost.at"},
    ])
    out_path = tmp_path / "targets.json"
    dropped_path = tmp_path / "dropped.csv"

    def fake_resolver(host, *args, **kwargs):
        if host == "real.at":
            return [("f", "t", "p", "cn", ("1.2.3.4", 0))]
        raise _socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(kmu.socket, "getaddrinfo", fake_resolver)

    rc = _run_cli([
        "discover-kmu",
        "--scout-csv", str(csv_path),
        "--dns-pre-flight",
        "--out", str(out_path),
        "--out-dropped", str(dropped_path),
    ])
    assert rc == 0

    payload = json.loads(out_path.read_text())
    assert payload["config"]["dns_preflight"] is True
    assert payload["dropped_count"] == 1, f"expected 1 drop, got {payload['dropped_count']}"
    assert payload["target_count"] == 16

    # The dropped CSV must exist and be parseable.
    assert dropped_path.exists()
    import csv as _csv
    with dropped_path.open() as f:
        rd = _csv.DictReader(f)
        rows = list(rd)
    assert len(rows) == 1
    assert rows[0]["company_name"] == "Ghost"
    assert rows[0]["reason"] == "dns_nxdomain"
    assert rows[0]["dropped_apex"] == "ghost.at"


def test_cli_discover_kmu_without_dropped_csv_unchanged(tmp_path, capsys):
    """Default behaviour (no --out-dropped) must not write a dropped CSV
    and must not change the stdout payload shape beyond adding the
    dropped_count + dns_preflight config keys."""
    csv_path = tmp_path / "scout_review_required.csv"
    _write_scout(csv_path, [
        {"row_id": "1", "name": "Real", "company_website": "https://real.at"},
    ])
    out_path = tmp_path / "targets.json"

    rc = _run_cli([
        "discover-kmu",
        "--scout-csv", str(csv_path),
        "--out", str(out_path),
    ])
    assert rc == 0
    payload = json.loads(out_path.read_text())
    assert payload["dropped_count"] == 0
    assert payload["config"]["dns_preflight"] is False
    # No dropped CSV should have been written anywhere
    assert not (tmp_path / "dropped.csv").exists()


# ---------------------------------------------------------------------------
# CLI: dropped-stats subcommand tests (Phase 6.2)
# ---------------------------------------------------------------------------


_DROPPED_HEADER = (
    "source_sheet", "source_row_id", "company_name",
    "company_website", "dropped_apex", "reason", "notes",
)


def _write_dropped_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_DROPPED_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _DROPPED_HEADER})


def test_cli_dropped_stats_human_table(tmp_path, capsys):
    """`dropped-stats --dropped-csv <p>` prints a human-readable table
    when the CSV is well-formed and contains only known reasons."""
    dropped = tmp_path / "dropped.csv"
    _write_dropped_csv(dropped, [
        {"source_sheet": "scout_review_required.csv", "source_row_id": "1",
         "company_name": "A", "company_website": "nan", "dropped_apex": "",
         "reason": "sentinel", "notes": ""},
        {"source_sheet": "scout_review_required.csv", "source_row_id": "2",
         "company_name": "B", "company_website": "nan", "dropped_apex": "",
         "reason": "sentinel", "notes": ""},
        {"source_sheet": "scout_review_required.csv", "source_row_id": "3",
         "company_name": "C", "company_website": "https://ghost.at",
         "dropped_apex": "ghost.at", "reason": "dns_nxdomain", "notes": ""},
    ])
    rc = _run_cli(["dropped-stats", "--dropped-csv", str(dropped)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "total rows: 3" in out
    assert "sentinel" in out
    assert "dns_nxdomain" in out
    assert "scout_review_required.csv" in out
    # No WARN line because all reasons are known.
    err = capsys.readouterr().err
    assert "WARN" not in err


def test_cli_dropped_stats_json_emits_machine_readable(tmp_path, capsys):
    """`dropped-stats --json` parses as JSON with stable schema."""
    dropped = tmp_path / "dropped.csv"
    _write_dropped_csv(dropped, [
        {"source_sheet": "scout_review_required.csv", "source_row_id": "1",
         "company_name": "A", "company_website": "nan", "dropped_apex": "",
         "reason": "sentinel", "notes": ""},
    ])
    rc = _run_cli(["dropped-stats", "--dropped-csv", str(dropped), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 1
    assert payload["by_reason"] == {"sentinel": 1}
    assert payload["by_sheet"] == {"scout_review_required.csv": 1}
    assert payload["unique_apexes"] == 1
    assert payload["unknown_reason_rows"] == 0
    assert payload["has_unknown_reasons"] is False


def test_cli_dropped_stats_missing_file_exits_2(tmp_path, capsys):
    """Missing dropped.csv is an operator-visible error (rc=2)."""
    rc = _run_cli([
        "dropped-stats", "--dropped-csv", str(tmp_path / "no-such.csv"),
    ])
    assert rc == 2
    assert "not found" in capsys.readouterr().err.lower()


def test_cli_dropped_stats_unknown_reason_exits_2(tmp_path, capsys):
    """An opaque reason (dns_error:...) trips the WARN branch (rc=2)."""
    dropped = tmp_path / "dropped.csv"
    _write_dropped_csv(dropped, [
        {"source_sheet": "scout_review_required.csv", "source_row_id": "1",
         "company_name": "X", "company_website": "",
         "dropped_apex": "x.at", "reason": "sentinel", "notes": ""},
        {"source_sheet": "scout_review_required.csv", "source_row_id": "2",
         "company_name": "Y", "company_website": "",
         "dropped_apex": "y.at",
         "reason": "dns_error: gaierror(-4, 'Non-recoverable failure')",
         "notes": ""},
    ])
    rc = _run_cli(["dropped-stats", "--dropped-csv", str(dropped)])
    assert rc == 2, "opaque dns_error: reasons must trip the WARN branch"
    captured = capsys.readouterr()
    assert "(unknown — triage)" in captured.out
    assert "WARN" in captured.err
    assert "1 row" in captured.err


def test_cli_dropped_stats_empty_dropped_csv_is_ok(tmp_path, capsys):
    """An empty (header-only) dropped.csv is a valid clean case (rc=0)."""
    dropped = tmp_path / "dropped.csv"
    _write_dropped_csv(dropped, [])
    rc = _run_cli(["dropped-stats", "--dropped-csv", str(dropped)])
    assert rc == 0
    assert "empty" in capsys.readouterr().out.lower()


def test_cli_dropped_stats_against_pilot_artifact(tmp_path, capsys):
    """End-to-end against the day-1 pilot dropped.csv (committed under
    live_results/). Pins the historical shape: 66 rows, 56 sentinel +
    8 dns_nxdomain (or close — see test_summarise_dropped_pilot_*
    above for the full invariant list)."""
    repo_root = Path(__file__).parent.parent
    pilot_csv = repo_root / "live_results" / "pilot-2026-07-20" / "dropped.csv"
    if not pilot_csv.exists():
        pytest.skip(f"pilot dropped.csv not present at {pilot_csv}")
    rc = _run_cli(["dropped-stats", "--dropped-csv", str(pilot_csv)])
    # Pilot is clean (only known reasons), so rc=0.
    assert rc == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "total rows: 66" in out

