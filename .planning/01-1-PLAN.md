# Plan of Action — austria-job-scout

**Goal:** given a reference job, find ≥ 10 similar Austrian roles with working links within 5 minutes, with zero duplicate URLs across re-runs.

**Strategy:** modular code (one skill, many subcommands), sidecar import of `job-research-framework` (no fork), separate SQLite DB with `fetch_log` for dedupe.

## Phase Map

| # | Phase | Status | Deliverable | Verification |
|---|-------|--------|-------------|--------------|
| 0 | Setup | ✅ Done | Project dir, .planning, research/, symlink to ~/.hermes/projects/ | `ls ~/.hermes/projects/austria-job-scout/` |
| 1 | Skill shell + DB schema + ingest module | ✅ Done | `austria_job_scout/__main__.py`, `db.py`, `schema.sql` | `python -m austria_job_scout --help` works; 49 tests passing |
| 2 | Target discovery + fetcher (with all Pillar 0 + 0b guards) | ✅ Done | `probes/`, `seeds.py`, `modules/target_discovery.py`, `modules/fetcher.py`, CLI `discover`+`fetch` | `python -m austria_job_scout discover ...` runs; 128 tests passing |
| 3 | ATS extractors + indexer + post-parse Jaccard filter | pending | `extractors/{ats_*,karriere_at,...}.py`, `modules/extractor.py`, `modules/indexer.py` | Greenhouse JSON → JobPosting; insert with `status='skipped_low_overlap'` for <MIN_OVERLAP |
| 4 | Similarity + reporter + end-to-end pipeline | pending | `modules/similarity.py`, `modules/reporter.py`, `pipeline` subcommand | `--role "Senior Rust Engineer" --out report.md` produces Markdown with ≥10 results |
| 5 | SKILL.md refresh + skill registry verification | pending | re-publish at `~/.hermes/skills/productivity/austria-job-scout/` | `skills_list` shows the skill |

## Iteration 1 (NOW) — What ships today

**Scope:** smallest runnable slice that proves the architecture.

- `austria_job_scout/__main__.py` — argparse CLI with subcommands (only `ingest`, `db-init`, `db-stats` enabled in iter 1)
- `austria_job_scout/db.py` — SQLite singleton, migrations
- `austria_job_scout/schema.sql` — canonical schema (austria_jobs, fetch_log, reference_jobs, skill_aliases)
- `austria_job_scout/modules/ingest.py` — PDF / TXT / role-name → ReferenceJob JSON
- `austria_job_scout/probes/language.py` — heuristic language detection (de/en)
- Tests: `tests/test_db.py`, `tests/test_ingest.py` with at least 3 golden fixtures

**Out of scope for iter 1 (defer to iter 2+):**
- target discovery / fetch / extract / index / score / report — stubs only with `NotImplementedError` so the import surface is real and `iter-2` work has stable contracts
- ATS extractors — deferred
- CLI integration tests — iter 8

## Iteration 2 — Target discovery + fetcher

- `probes/career_paths.py` — HEAD probe the 8 paths per company domain
- `probes/ats_classifier.py` — URL/HTML → ATS fingerprint
- `probes/aggregator_search.py` — build karriere.at / jobs.at search URLs from ReferenceJob
- `modules/target_discovery.py` — orchestrate CT log → HEAD probe → classifier
- `modules/fetcher.py` — call `jrf.scripts.stealth_fetch` or vanilla `requests`, consult `fetch_log`, write log row
- Add `--discover` and `--fetch` subcommands

## Iteration 3 — Extractors + Indexer

- `extractors/generic_html.py` — BeautifulSoup fallback
- `extractors/ats_*.py` — one per ATS (Greenhouse JSON, Lever JSON, Personio XML, SmartRecruiters JSON, SuccessFactors XML)
- `extractors/karriere_at.py`, `extractors/jobs_at.py` — aggregator HTML
- `modules/extractor.py` — dispatcher (URL → extractor by ATS fingerprint)
- `modules/indexer.py` — insert with `INSERT OR IGNORE INTO austria_jobs (url) VALUES (?)` + UNIQUE constraint

## Iteration 4 — Similarity + Reporter + End-to-end

- `modules/similarity.py` — hybrid (cosine + FTS5 + Jaccard)
- `modules/reporter.py` — Markdown / JSON / CSV writers
- `pipeline` subcommand — runs all 8 modules in sequence
- `SKILL.md` — final skill registration

## Verification gates (each phase must pass before next)

| Gate | Command |
|------|---------|
| Smoke | `pytest tests/ -q` |
| Schema drift | `sqlite3 data/austria_jobs.db "PRAGMA integrity_check"` |
| Dedupe works | `python -m austria_job_scout fetch --url X; python -m austria_job_scout fetch --url X` → 1 row in fetch_log |
| No regression | `cd ~/.hermes/projects/job-research-framework && ./.venv/bin/python -m pytest tests/test_smoke.py -q` |

## Open questions (will surface to user only if blocking)

- Q1: Should the user provide their own OpenAI / OpenRouter API key for embeddings? (Default: yes, with a local sentence-transformers fallback.)
- Q2: Should we respect robots.txt for karriere.at? (Default: yes — they explicitly allow everything except BLEX/Ahrefs.)
- Q3: Should the default be `INSERT OR REPLACE` or `INSERT OR IGNORE` on dedupe? (Default: IGNORE — never overwrite a verified good row with a stale one.)

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| karriere.at sitemap hangs | High | Streaming gzipped consumer + 60s timeout |
| jrf `tokenizers` pin breaks | Already broken | Skip cross-encoder (`rerank_top_k=0`) |
| stealth-core binary broken | Already broken | Use `jrf.stealth_fetch`; design `Fetcher` for swap |
| User input is too vague ("ich such was mit Computern") | Real | Show "I parsed this as: {role}. Refine or accept?" before fetching |
| opendata.host API key missing | Possible | Optional — degrade gracefully (skip company-status enrichment) |

## Where the user can interrupt safely

After iter 1: try `python -m austria_job_scout ingest --role "Senior Rust Engineer"` — should return JSON in <1s.
After iter 2: try `python -m austria_job_scout discover --reference ref.json` — should return 5+ targets in <10s.
After iter 4: try `python -m austria_job_scout pipeline --role "Senior Rust Engineer"` — should produce a Markdown report in <5 min.
