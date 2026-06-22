"""target_discovery — ReferenceJob → List[Target] (iter-2, working).

The orchestrator. Builds a small, ranked list of Targets to fetch for a
given ReferenceJob. Honours every constraint in config:

  - SOURCE_PRIORITY 4-tier ordering (Tier 1 ATS JSON first)
  - predicted_relevance ≥ config.MIN_PREDICTED_RELEVANCE  (Pillar 0b rule 8)
  - is_cf_protected() blocks WAF sites (Pillar 0 rule 4)
  - Hard cap at MAX_TARGETS_PER_RUN
  - Deduped by URL hash (already-existing targets skip)

Sources consulted (in priority order):
  1. seeds.py — curated Austrian employers with known ATS tokens
  2. probes.aggregator_search — karriere.at / jobs.at / AMS search URLs
  3. probes.career_paths — HEAD-probed /karriere etc. (Tier 3, network)

Outputs are JSON-serialisable dicts that the fetcher can consume directly.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .. import config, seeds as seedlib
from ..probes import aggregator_search, ats_classifier
from ..probes.career_paths import probe_domain

if TYPE_CHECKING:
    from .ingest import ReferenceJob


logger = logging.getLogger(__name__)


# Default predicted-relevance threshold; gates how aggressive the filter is.
# Mirrors config but the orchestrator may override per-call (testing).
DEFAULT_MIN_PREDICTED_RELEVANCE = 0.15


def _relevance_for_seed(seed: seedlib.SeedCompany, reference) -> float:
    """Compute predicted_relevance for a seed company vs the reference.

    Heuristic (no LLM, deterministic):
      1. Start at 0.4 baseline (Tier-1 ATS endpoints are highly likely to have
         relevant roles; career-path probes less so).
      2. +0.3 if seed has known ATS (we know we're hitting a real jobs feed).
      3. +0.2 if any seed sector token appears in reference skills/title.
      4. -0.2 if seed sector clearly mismatches (banking vs software ref).
    Clamped to [0.0, 1.0].
    """
    base = 0.4
    if seed.ats and seed.board_token:
        base += 0.3
    ref_text = " ".join(
        filter(None, [
            (reference.role_query or "") if reference else "",
            (reference.title or "") if reference else "",
            " ".join(reference.skills or []) if reference and reference.skills else "",
        ])
    ).lower()
    sec = seed.sector.lower()
    if sec and any(tok in ref_text for tok in sec.split("-")):
        base += 0.2
    if sec in {"banking", "insurance", "manufacturing"} and "rust" in ref_text:
        base -= 0.2
    return max(0.0, min(1.0, base))


def _target_from_seed(seed: seedlib.SeedCompany, reference) -> dict | None:
    """Build one target dict from a seed. Returns None if ATS not actionable."""
    api_url = (
        ats_classifier.api_endpoint_for(seed.ats, seed.board_token)
        if seed.ats and seed.board_token else None
    )
    if not api_url:
        # Tier 3 — career-path probe target. We don't expand the URL here;
        # the orchestrator's `discover()` will call probe_domain when we
        # actually want to crawl. For target listing we just leave a marker.
        api_url = None
    if not api_url:
        return None
    rel = _relevance_for_seed(seed, reference)
    return {
        "ats": seed.ats,
        "source_kind": "ats_board",
        "url": api_url,
        "company_name": seed.name,
        "company_domain": seed.domain,
        "predicted_relevance": rel,
        "priority": config.SOURCE_PRIORITY.get(seed.ats, 30),
        "notes": f"seed:{seed.name}; sector={seed.sector}",
    }


def _attach_relevance(target: dict, reference) -> dict:
    """Adjust predicted_relevance for aggregator URLs using the reference."""
    base = target.get("predicted_relevance", 0.5)
    role = (getattr(reference, "role_query", None) or
            getattr(reference, "title", None) or "")
    role_query = (target.get("role_query") or "").lower()
    role_lc = role.lower()
    if role_query and role_query in role_lc:
        base += 0.1
    elif role and any(tok in role_query for tok in role_lc.split() if len(tok) > 3):
        base += 0.05
    target["predicted_relevance"] = max(0.0, min(1.0, base))
    return target


def _passes_filters(target: dict, min_relevance: float) -> bool:
    """Apply the discrimination gates (Pillar 0b + Pillar 0)."""
    if target["predicted_relevance"] < min_relevance:
        return False
    if not config.AGGRESSIVE_MODE and config.is_cf_protected(target["url"]):
        # Tier-4 sites blocked in residential mode.
        return False
    return True


def _dedupe(targets: list[dict]) -> list[dict]:
    """Drop duplicate URLs (preserve highest priority)."""
    by_url: dict[str, dict] = {}
    for t in targets:
        u = t["url"]
        cur = by_url.get(u)
        if cur is None or t["priority"] < cur["priority"]:
            by_url[u] = t
    return list(by_url.values())


def discover(
    reference,
    *,
    min_relevance: float = DEFAULT_MIN_PREDICTED_RELEVANCE,
    include_aggregators: bool = True,
    include_seeds: bool = True,
    probe_seed_paths: bool = False,   # set True to actually HEAD-probe; default off for safety
    max_targets: int | None = None,
) -> list[dict]:
    """Build a ranked list of Targets to fetch for *reference*.

    The returned list is sorted by (priority ASC, predicted_relevance DESC)
    and truncated to max_targets (or config.MAX_TARGETS_PER_RUN).

    Parameters
    ----------
    reference : ReferenceJob
        From :func:`austria_job_scout.modules.ingest.ingest_input`.
    min_relevance : float
        Drop targets below this predicted relevance (Pillar 0b rule 8).
    include_aggregators : bool
        Include karriere.at / jobs.at / AMS aggregator queries.
    include_seeds : bool
        Include Tier-1 ATS endpoints from the curated seed list.
    probe_seed_paths : bool
        If True, run a HEAD probe on Tier-3 seed companies (network).
        Off by default — call :func:`discover(..., probe_seed_paths=True)`
        explicitly when ready.
    max_targets : int | None
        Hard cap; falls back to config.MAX_TARGETS_PER_RUN.
    """
    out: list[dict] = []

    # 1. Seed-based Tier-1 ATS endpoints (zero-stealth JSON/XML).
    if include_seeds:
        for seed in seedlib.SEED_AUSTRIAN_COMPANIES:
            t = _target_from_seed(seed, reference)
            if t is not None:
                out.append(t)

    # 2. Aggregator search URLs (Tier 2; soft anti-bot).
    if include_aggregators:
        for t in aggregator_search.all_aggregator_targets(reference):
            out.append(_attach_relevance(t, reference))

    # 3. Optional: Tier-3 career-path probes for seed companies without ATS.
    if probe_seed_paths:
        for seed in seedlib.all_without_ats():
            try:
                results = probe_domain(seed.domain)
                for r in results:
                    if 200 <= (r.status_code or 0) < 400:
                        out.append({
                            "ats": r.ats_fingerprint or "generic_html",
                            "source_kind": "career_path",
                            "url": r.redirected_to or r.url,
                            "company_name": seed.name,
                            "company_domain": seed.domain,
                            "predicted_relevance": _relevance_for_seed(seed, reference),
                            "priority": config.SOURCE_PRIORITY.get("career_path", 30),
                            "notes": f"career-path probe of {seed.name}",
                        })
            except Exception as e:
                logger.debug("career-path probe failed for %s: %s", seed.domain, e)

    # 4. Dedupe by URL
    out = _dedupe(out)

    # 5. Filter (discrimination-before-fetch)
    out = [t for t in out if _passes_filters(t, min_relevance)]

    # 6. Sort by priority ASC, then predicted_relevance DESC
    out.sort(key=lambda t: (t["priority"], -t["predicted_relevance"]))

    # 7. Truncate
    cap = max_targets if max_targets is not None else config.MAX_TARGETS_PER_RUN
    return out[:cap]
