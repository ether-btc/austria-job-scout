# STATE.md — current session state

**Date:** 2026-06-22
**Model:** MiniMax-M3 (MiniMax via minimax.io — note: switched 3× in this turn: glm-4.7 → glm-5.2 → glm-5.1 → MiniMax-M3)
**Session goal:** user asked for a skill that searches Austrian career sites for similar job offers.

## Decisions made this session

1. **Single skill, many subcommands** — `austria-job-scout` with `ingest`, `discover`, `fetch`, `extract`, `index`, `score`, `report`, `pipeline`. Modular code, not modular skills.
2. **Sidecar import of jrf** — the new skill imports `job-research-framework` as a library; zero fork, zero code duplication. The two skills stay independently deployable.
3. **Separate SQLite DB** — `data/austria_jobs.db` is the new skill's index. jrf's `data/patterns.db` is left untouched. Cross-skill queries use read-only `ATTACH` if ever needed.
4. **Dedupe-first fetcher** — every network call consults `fetch_log` first; same URL within 24h returns the cached RawResponse.
5. **Hybrid similarity** — 50% semantic embedding cosine + 30% FTS5 BM25 + 20% skill-set Jaccard. Cross-encoder deferred (tokenizers pin in jrf venv).
6. **⛔ Residential IP protection is PARAMOUNT** (user restated 2026-06-22). Patched `config.py` defaults: `AGGRESSIVE_MODE=0` default, daily budget 50/day residential / 10/day for WAF sites / 500/day aggressive, delays 5–25s with 15% long-pause 60–180s, circuit breaker 3 failures → 24h cool-off, WAF sites (`stepstone.at`, `indeed.com`, `myworkdayjobs.com`, `willhaben.at`, `jobs.at`) hard-blocked unless `AGGRESSIVE_MODE`. New Pillar 0 in PITFALLS.md.
7. **⛔ Discrimination-before-fetch is PARAMOUNT** (user restated 2026-06-22 in same session). Patched `config.py` with: `MAX_FETCH_PER_RUN=25` residential / 200 aggressive, `MAX_TARGETS_PER_RUN=30/300`, `MIN_SKILL_OVERLAP_FOR_INDEX=0.20` (below → `status='skipped_low_overlap'`), `MAX_AGGREGATOR_PAGES=1/3`, `TOP_N_PER_SOURCE=10/50`, `STAGGER_REMAINDER=True` (wishlist the long tail, never drop silently), `SOURCE_PRIORITY` 4-tier ordering. New Pillar 0b in PITFALLS.md. The `wishlist` table added to schema (iter-2 — defer).
8. **No stealth-core binary in v1** — the binary is broken on this RPi (GLIBC 2.39 vs 2.36). Python `curl_cffi` via jrf is the v1 path, with `impersonate="chrome120"` so the TLS+HTTP2+headers fingerprint matches a real browser. The `Fetcher` interface is designed so the swap is one-liner when stealth-core is rebuilt.

## Iter-2 status (in progress 2026-06-22 evening)

- ✅ `probes/ats_classifier.py` — pure pattern matching, 13 ATS fingerprints
- ✅ `probes/aggregator_search.py` — karriere.at / jobs.at / AMS URL builders
- ✅ `probes/career_paths.py` — HEAD probe with jrf stealth_fetch fallback to vanilla requests
- ✅ `seeds.py` — 30 curated Austrian employer entries (no fabrication)
- ✅ `modules/target_discovery.py` — orchestrator with Pillar 0b discriminator
- ✅ `modules/fetcher.py` — full Pillar 0 + 0b guards wired to schema:
  - AGGRESSIVE_MODE + is_cf_protected block
  - circuit_breaker (read + write + trip)
  - daily_budget (read + increment)
  - fetch_log dedupe
  - navigation noise (GET / + GET /jobs before target)
  - human-like long-tail delay
  - detection_events logging
  - ATS fingerprinting on response
- ✅ CLI: `discover` and `fetch` subcommands, `--out` flag, stdin/file/inline JSON input
- ✅ 128 tests passing (was 49 at end of iter-1; +79 new tests for iter-2)
- Bug fixes during review:
  - `quote_via` URL encoding bug in aggregator_search
  - `indeed.at` hostname missing from ats_classifier rules
  - argparse `--db` propagation (from iter-1 review)
  - `Path(long_json).is_file()` OSError → check `startswith({[)` first
  - `fetch()` was silently swallowing DailyBudgetExhausted → now re-raises (Pillar 0 rule 9)
  - Test mocks used wrong host (acme.com vs boards-api.greenhouse.io)
  - Floating-point equality for predicted_relevance
  - `_sleep_like_human` was burning 5-25s/test → added `no_sleep` fixture
  - `ingest_to_db` race across second boundaries → time-mock the test

## Blockers

- User input file (PDF / description / role-name) was never attached. v1 design handles all three.
- stealth-core binary remains broken on this RPi. Fetcher uses jrf.stealth_fetch (curl_cffi); `Fetcher` interface is the single swap point when fixed.
- jrf's `tokenizers` pin still breaks 6 RAG tests on import. Not on the iter-2 critical path.
