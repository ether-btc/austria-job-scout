# Austria-Job-Scout — Comprehensive Audit Report (Final)

**Date:** 2026-07-03
**Auditor:** Hermes Agent
**Skills:** code-quality-assessment, ponytail, hierarchical-code-analysis
**Scope:** Full project audit + fixes + connecting projects

---

## Executive Summary

**Overall Rating: 4.0 / 5** | **Recommendation: SHIP — all P0 + P1 issues fixed**

All 243 tests pass (5:47 runtime). Three of the original audit's critical
findings were genuine and have been fixed. Two were false positives in
the original audit and have been documented as such.

---

## Original Audit Findings — Status

### C-1. CRITICAL — `rss_discovery.py` was a copy-paste of `career_page_extractor.py`

**Status: ✅ FIXED**

The 354-line file was a literal copy of `career_page_extractor.py` with
the wrong module docstring, wrong imports (`from .ats_extractor` instead
of `from ..extractors.ats_extractor`), and the wrong function API.

**Fix:** Rewrote from scratch as a proper RSS/Atom discovery module:
- URL builders: `build_austrian_company_rss_urls`, `build_aggregator_rss_urls`,
  `build_wien_specific_rss_urls`, `build_all_austrian_rss_targets`
- Parsers: `extract_rss_jobs` (RSS 2.0 + Atom), `get_rss_info`, `is_rss_feed`
- All 15 RSS tests pass.

### C-2. CRITICAL — `ats_extractor.py` imports `requests` but never uses it

**Status: ❌ FALSE POSITIVE — original audit was wrong**

`extract_from_url()` at line 600 uses `requests.get()` to fetch a URL
and parse it. While the pipeline uses the Pillar-0-compliant
`extract_from_html()` path, `extract_from_url()` is a legitimate
standalone convenience helper. **Kept the import.**

### C-3. CRITICAL — Root-level duplicate `kmu_wien_discovery_*.py` files

**Status: ✅ FIXED**

Deleted `kmu_wien_discovery_backup.py` and `kmu_wien_discovery_clean.py`
(67 KB of dead weight). Canonical version is in
`austria_job_scout/modules/kmu_wien_discovery.py`.

---

## High-Severity Issues

### H-1. `kmu_wien_discovery.py` — hardcoded test values in production code

**Status: ✅ FIXED (and the file was fundamentally broken)**

The "Sprint 3 Audit Completion" commit (5e772c7) was the working version
but contained:
- `items[:2]` / `items[:3]` slice limits hardcoded for tests
- Hardcoded fake domains (`"firm1.at"`, `"wko2.at"`)
- The original `bb484e8` version was syntactically broken
  (indented `from __future__`, missing if-bodies)

**Fix:** Rewrote from scratch as a clean module that:
- Parses JSON-LD `<script type="application/ld+json">` Organization entries
- Falls back to HTML card extraction (`.unternehmen-item`, `.firma-entry`,
  `.firmenname`, `.wko-member`)
- Synthesises a domain from the company name when no website link is present
- Preserves raw sector text from the source (verbatim, no taxonomy)
- Raises `ValueError("Unknown Wien KMU source: ...")` on invalid source

All 12 KMU tests now pass.

### H-2. `aggregator_extractor.py` imports `requests` (unused)

**Status: ❌ FALSE POSITIVE — original audit was wrong**

Same as C-2: `extract_from_url()` legitimately uses `requests`. **Kept.**

### H-3. JRF symlink broken on this RPi

**Status: ✅ FIXED (with caveat)**

The `~/.hermes/projects/job-research-framework` symlink points to
`/media/hermes-pi/f3fd4a1d-.../hermes/projects/job-research-framework`
but that directory does not exist on this system (USB drive not mounted).
The fetcher silently fell back to vanilla `requests`, leaving the user
unaware that curl_cffi TLS impersonation was disabled.

**Fix:** Added `_find_jrf_path()` in `fetcher.py` that:
1. Checks the standard symlink path
2. Probes `/media/hermes-pi/*/hermes/projects/job-research-framework`
3. Probes `/mnt/*/hermes/projects/job-research-framework` (bounded to
   one level deep — avoids unbounded `rglob` on systems with many mounts)
4. Emits a **WARNING** (not debug) when stealth_fetch is unavailable,
   with actionable guidance: "Run the job-research-framework project
   locally, or set up a residential proxy, before scraping CF-protected
   sites."

---

## Tests Now Passing (243/243)

| Test File | Count | Status |
|-----------|------:|--------|
| test_aggregator_search.py | 10 | ✅ |
| test_ats_classifier.py | 34 | ✅ |
| test_benchmark.py | 4 | ✅ |
| test_career_page_extractor.py | 15 | ✅ |
| test_cli.py | 14 | ✅ |
| test_content_dedup.py | 10 | ✅ |
| test_db.py | 11 | ✅ |
| test_de_en_synonyms.py | 14 | ✅ |
| test_extract_index_score.py | 13 | ✅ |
| test_extractors_json.py | 16 | ✅ |
| test_fetcher.py | 18 | ✅ |
| test_indexer.py | 6 | ✅ |
| test_ingest.py | 28 | ✅ |
| test_kmu_wien_discovery.py | 12 | ✅ |
| test_pipeline_e2e.py | 2 | ✅ |
| test_rss_discovery.py | 15 | ✅ |
| test_target_discovery.py | 13 | ✅ |
| test_wishlist_persistence.py | 8 | ✅ |
| **Total** | **243** | **✅** |

Pre-fix: `test_rss_discovery.py` couldn't be collected (ModuleNotFoundError).
The other tests were either passing or had assertion mismatches that
the kmu and pipeline rewrites resolved.

---

## Ponytail / Over-Engineering Analysis

Previous audit (2026-06-30) found 3 items, all addressed. New findings:

### OE-1. Dead helper `_classify_sector()` in kmu_wien_discovery.py

**Status: ✅ REMOVED**

After the kmu rewrite, the sector taxonomy normaliser was unused
(sector is passed through verbatim from the source). Deleted.

### OE-2. Redundant `from bs4 import BeautifulSoup` and `import re` in functions

**Status: ✅ N/A — kmu was rewritten from scratch with module-level imports

The new kmu_wien_discovery.py has clean module-level imports.

### OE-3. `SimilarityAnalyzer` class is dead code

**Status: ❌ FALSE POSITIVE — original audit was wrong**

`SimilarityAnalyzer` is used at `similarity.py:295` inside the
`analyze_similarity()` convenience function. **Kept.**

### OE-4. `_get_max_fetches()` trivial method

**Status: ✅ Already fixed in prior ponytail audit (commit 2e97e82)

### OE-5. `run_pipeline()` wrapper was a 35-line one-caller wrapper

**Status: ❌ WRONG to delete — tests require it**

The 2026-06-30 ponytail audit recommended deleting `run_pipeline()`.
It was deleted in commit `2e97e82`, breaking `test_pipeline_e2e.py`
which imports and uses it. **Restored from pre-ponytail state.** The
ponytail principle "one caller, no added logic" is wrong here because
the test suite is a permanent caller that should not be deleted.

---

## Connecting Projects

### job-research-framework (JRF)

**Integration:** `fetcher.py:99-142` imports JRF's `stealth_fetch`.

**Status:** JRF is on a USB drive that is not currently mounted on this
RPi. The fetcher now correctly falls back to vanilla `requests` and
emits a warning. To restore stealth-fetch, plug in the USB drive that
contains the JRF project (or `cd` into the project and run from there
so `Path.cwd()` includes `scripts/stealth_fetch.py`).

### cv-profile-assessment

**Status:** Not integrated. The austria-job-scout `ingest.py` module
accepts free-text role names; cv-profile-assessment produces structured
profile JSON. A future integration could let cv-profile-assessment
output feed directly into the discovery stage. Out of scope for this
audit.

---

## Lessons Learned (for future audits)

1. **Verify imports before claiming dead code.** Grep for *every* use
   of the symbol, not just the one obvious call site. The original
   audit's C-2 / H-2 / OE-3 findings were all false positives caused
   by this.
2. **Test files are the contract.** When the original implementation
   was syntactically broken AND the tests were written against the
   *intended* (broken) API, the correct fix is to implement the API
   the tests describe, not delete the tests.
3. **Symlink follow behavior is silent.** Always check `os.path.exists()`
   vs `Path.resolve()` — broken symlinks raise on follow but not on
   stat. Warn loudly when an optional dependency isn't loadable.
4. **`rglob` is dangerous on `/mnt`.** Bound traversal depth.
5. **Restored `run_pipeline()` even though ponytail said to delete it.**
   Tests are permanent callers. Ponytail principle applies to library
   code, not test contracts.

---

*Generated by Hermes Agent. Final status: 243/243 tests pass.*
