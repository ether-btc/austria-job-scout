"""Tests for target_discovery — pure orchestrator with mocked probes.

The orchestrator reads seeds + aggregator builders + (optionally) career-path
probes. None of those should hit the real network in tests.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from austria_job_scout import config, seeds as seedlib
from austria_job_scout.modules import target_discovery
from austria_job_scout.modules.ingest import ReferenceJob


def _ref(role="Senior Rust Engineer", skills=None, lang="en"):
    return ReferenceJob(
        source="role_name", raw_text=role, title=role, role_query=role,
        language=lang, skills=skills or ["Rust", "PostgreSQL", "Kubernetes"],
        language_queries={"de": role, "en": role},
    )


# ---------------------------------------------------------------------------
# No-network default
# ---------------------------------------------------------------------------

def test_discover_no_network_no_probe_returns_seeds_and_aggregators():
    ref = _ref()
    targets = target_discovery.discover(ref, probe_seed_paths=False)
    # We have 30 seeds + 3 aggregators = 33 candidates, minus CF-blocks + relevance-gated
    assert len(targets) > 0
    # Tier-1 ATS endpoints should appear first (priority 10)
    assert targets[0]["priority"] <= 25   # Tier 1 or Tier 2


def test_discover_sorts_by_priority_then_relevance():
    ref = _ref()
    targets = target_discovery.discover(ref, probe_seed_paths=False)
    for i in range(len(targets) - 1):
        a, b = targets[i], targets[i + 1]
        # Either priority decreases (better first), or same priority + rel descends
        assert a["priority"] <= b["priority"] or \
               (a["priority"] == b["priority"] and a["predicted_relevance"] >= b["predicted_relevance"])


def test_discover_respects_max_targets():
    ref = _ref()
    targets = target_discovery.discover(ref, max_targets=5)
    assert len(targets) == 5


def test_discover_blocks_cf_protected_in_residential_mode(monkeypatch):
    monkeypatch.setattr(config, "AGGRESSIVE_MODE", False)
    ref = _ref()
    targets = target_discovery.discover(ref, probe_seed_paths=False)
    for t in targets:
        assert not config.is_cf_protected(t["url"]), \
            f"{t['url']} should be filtered out in residential mode"


def test_discover_allows_cf_protected_in_aggressive_mode(monkeypatch):
    monkeypatch.setattr(config, "AGGRESSIVE_MODE", True)
    ref = _ref()
    targets = target_discovery.discover(ref, probe_seed_paths=False)
    # jobs.at URLs should appear in aggressive mode
    ats_set = {t["ats"] for t in targets}
    assert "jobs_at" in ats_set or any("jobs.at" in t["url"] for t in targets)


def test_discover_relevance_gate_filters_low():
    ref = _ref()
    targets = target_discovery.discover(ref, min_relevance=0.99,
                                        probe_seed_paths=False)
    # With a 0.99 gate, only the very best stay
    for t in targets:
        assert t["predicted_relevance"] >= 0.99


def test_discover_skips_seeds_when_disabled():
    ref = _ref()
    targets = target_discovery.discover(
        ref, include_seeds=False, probe_seed_paths=False
    )
    # No Tier-1 ATS endpoints should appear
    for t in targets:
        assert t["source_kind"] in ("aggregator_query", "career_path"), \
            f"unexpected source_kind={t['source_kind']!r} in {t}"


def test_discover_skips_aggregators_when_disabled():
    ref = _ref()
    targets = target_discovery.discover(
        ref, include_aggregators=False, probe_seed_paths=False
    )
    for t in targets:
        assert t["source_kind"] != "aggregator_query"


def test_discover_dedupes_duplicate_urls():
    ref = _ref()
    targets = target_discovery.discover(ref, probe_seed_paths=False)
    urls = [t["url"] for t in targets]
    assert len(urls) == len(set(urls)), "duplicate URLs in target list"


# ---------------------------------------------------------------------------
# With mocked career-path probes
# ---------------------------------------------------------------------------

def test_probe_seed_paths_adds_tier3_targets(monkeypatch):
    monkeypatch.setattr(config, "AGGRESSIVE_MODE", False)
    from austria_job_scout.probes.career_paths import PathProbeResult, probe_domain as real_probe

    def fake_probe(domain, timeout=30):
        if domain == "post.at":
            return [PathProbeResult(
                url="https://post.at/karriere",
                status_code=200,
                ats_fingerprint="generic_html",
                elapsed_ms=42,
            )]
        return []

    # Patch the symbol target_discovery actually calls.
    monkeypatch.setattr(target_discovery, "probe_domain", fake_probe)
    ref = _ref()
    targets = target_discovery.discover(ref, probe_seed_paths=True)
    has_post = any("post.at" in t["url"] for t in targets)
    assert has_post


# ---------------------------------------------------------------------------
# Predicted-relevance heuristic
# ---------------------------------------------------------------------------

def test_known_ats_token_boosts_relevance():
    ref = _ref()
    targets = target_discovery.discover(ref, probe_seed_paths=False)
    by_url = {t["url"]: t for t in targets}
    # Check a known-valid ATS board
    bitpanda = by_url.get("https://boards-api.greenhouse.io/v1/boards/bitpanda/jobs?content=true")
    if bitpanda:
        # Bitpanda is a known ATS — relevance should be >= 0.7
        assert bitpanda["predicted_relevance"] >= 0.7


def test_banking_sector_penalized_for_rust_role():
    ref = _ref(role="Senior Rust Engineer", skills=["Rust"])
    # The heuristic subtracts 0.2 if seed.sector ∈ banking/insurance/manufacturing
    # AND ref mentions rust. Test the helper directly:
    seed = seedlib.by_name("Erste Group")
    assert seed is not None
    rel = target_discovery._relevance_for_seed(seed, ref)
    # baseline 0.4 (no ATS known) + 0.0 (sector not in ref text) - 0.2 (rust vs banking)
    assert rel < 0.5


def test_matching_sector_boosts_relevance():
    ref = _ref(role="Senior Crypto Trader", skills=["Crypto"])
    seed = seedlib.by_name("Bitpanda")
    assert seed is not None
    rel = target_discovery._relevance_for_seed(seed, ref)
    # 0.4 (base) + 0.3 (known ATS) + 0.2 (sector 'crypto' matches) = 0.9
    assert rel == pytest.approx(0.9)
