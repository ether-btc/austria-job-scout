# PROJECT.md — Austria Job Scout

## Vision

Given a reference job (PDF, text description, or just a role name like "Senior Rust Backend Developer"),
find **similar roles currently open at Austrian companies**, with stable links a human can open.

The output is a ranked list — not a search engine. Each row is one job posting a human could apply to today.

## Goals (v1)

1. **Input**: PDF / Markdown / TXT / role-name string.
2. **Discriminate before fetch.** Given the reference, build a small (≤25) high-quality candidate
   target list — Tier-1 ATS JSON first, then aggregator queries, then direct career pages. WAF sites
   deferred to v2 unless `AGGRESSIVE_MODE`. Save the long tail to a wishlist for next run. Never
   fetch hundreds at once. (See PITFALLS §Pillar 0b.)
3. **Fetch**: Pull current job postings without scraping the same URL twice. Per-domain rate cap,
   per-day budget, per-domain circuit breaker.
4. **Extract**: Structured JobPosting {title, company, location, full_text, skills[], url,
   source_domain, ats, lang, fetched_at, hash}.
5. **Filter**: drop postings below `MIN_SKILL_OVERLAP_FOR_INDEX` to `austria_jobs` with
   `status='skipped_low_overlap'` (visible but not scored).
6. **Index**: SQLite with `UNIQUE(url)` dedupe so re-runs are idempotent.
7. **Similarity**: Hybrid score (semantic embedding cosine + FTS5 BM25 + skill-set Jaccard)
   against the reference.
8. **Report**: Human-readable Markdown + JSON + CSV with: role, description, valid link,
   score breakdown, ATS source, salary if listed, language, posted/refreshed date. Honest
   reporting includes wishlist counts + skipped counts — never silent 0 results.

## Goals (v2 — not v1)

- Workday / SmartRecruiters / Indeed / willhaben fetcher with full stealth-core + CDP browser pipeline
- Email/SMS alerts on new matching postings
- Real-time RSS subscriptions per company
- LLM-extracted "missing skills" against the user's CV

## Out of scope (v1)

- Applying to jobs automatically
- Interview prep / mock interviews (already covered by `job-research-framework` phase 4.1)
- Salary normalization / FX conversion
- Multi-language CV generation
- LinkedIn / Xing scraping (TOS-prohibitive)

## Constraints

- Runs on Raspberry Pi 5 (8GB RAM, ARM64, Debian Bookworm glibc 2.36).
- One fetch per URL across all runs (dedupe). Same ad in two ATS feeds → only one row.
- Per-domain rate limit ≤ 3 hits/min residential (≤10/min aggressive); long-tail 5–25s delays
  with 15% chance of 60–180s long pause.
- Daily budget: 50 requests/day residential / 10/day for WAF sites / 500/day aggressive.
- Per-run cap: 25 fetches residential / 200 aggressive. Save remainder to wishlist.
- WAF-protected sites (`stepstone.at`, `indeed.com`, `myworkdayjobs.com`, `willhaben.at`,
  `jobs.at`) BLOCKED in residential mode. Opt in with `AGGRESSIVE_MODE=1` + a proxy.
- Top-N per source: 10 residential / 50 aggressive. Aggregator pagination: 1 page
  residential / 3 aggressive.
- Min skill overlap for indexing: 20%. Below → `status='skipped_low_overlap'`.
- Stealth-core binary is currently **broken** on RPi (GLIBC 2.39 vs system 2.36) — use Python
  `curl_cffi` with `impersonate="chrome120"` until stealth-core is rebuilt or replaced by
  Playwright sidecar.
## Success metric

v1: given any input, produce ≥ 10 similar Austrian roles within 5 minutes, all with working public links, with no duplicate URLs in the output. Re-running the same input within 24 h produces 0 new fetches.

## Stakeholders

User (job-seeker). No production users. The skill is local to a single Pi.

## Architecture philosophy

- **Modular code** (not modular skills). One skill `austria-job-scout` exposes one CLI with subcommands; each subcommand is one module. Subcommands are independently testable.
- **Sidecar, not fork.** The skill imports `job-research-framework` (existing on USB) and `firmen-quickcheck` (existing on disk) as libraries. Zero code duplication.
- **Index-first.** A single SQLite database is the single source of truth: dedupe, fetch log, and similarity index all share it.
- **Idempotent fetches.** Every fetch operation consults the index before going to the network.

## File map

```
~/.hermes/projects/austria-job-scout/
├── SKILL.md                          # skill metadata (also linked into ~/.hermes/skills/)
├── README.md                         # short user-facing description
├── pyproject.toml                    # build metadata
├── deps.yaml                         # env vars + python deps
├── requirements.txt
├── austria_job_scout/
│   ├── __init__.py
│   ├── __main__.py                   # `python -m austria_job_scout` → CLI
│   ├── cli.py                        # argparse, subcommands
│   ├── config.py                     # paths, env loading, .env support
│   ├── db.py                         # SQLite singleton + migrations
│   ├── schema.sql                    # canonical schema (austria_jobs, fetch_log, ...)
│   ├── modules/
│   │   ├── ingest.py                 # PDF/TXT/role-name → ReferenceJob
│   │   ├── target_discovery.py       # ReferenceJob → List[Target]
│   │   ├── fetcher.py                # Target → RawResponse (+ dedupe)
│   │   ├── extractor.py              # RawResponse → JobPosting
│   │   ├── indexer.py                # JobPosting → SQLite (dedupe)
│   │   ├── similarity.py             # ReferenceJob + Index → List[ScoredJob]
│   │   └── reporter.py               # List[ScoredJob] → Markdown/JSON/CSV
│   ├── extractors/
│   │   ├── generic_html.py           # BeautifulSoup fallback
│   │   ├── karriere_at.py            # karriere.at HTML
│   │   ├── stepstone_at.py           # StepStone.at HTML (may 403)
│   │   ├── jobs_at.py                # jobs.at HTML
│   │   ├── ats_greenhouse.py         # boards.greenhouse.io JSON
│   │   ├── ats_lever.py              # api.lever.co JSON
│   │   ├── ats_smartrecruiters.py    # api.smartrecruiters.com JSON
│   │   ├── ats_personio.py           # *.jobs.personio.de XML
│   │   └── ats_successfactors.py     # per-tenant XML (deferred v2)
│   ├── probes/
│   │   ├── career_paths.py           # HEAD probe /karriere /jobs etc.
│   │   ├── ct_log.py                 # re-export jrf.ct_log_miner
│   │   ├── ats_classifier.py         # URL/HTML → ATS fingerprint
│   │   └── aggregator_search.py      # build karriere.at / StepStone.at search URLs
│   └── fixtures/
│       ├── reference_jobs.json       # golden inputs for tests
│       ├── ats_responses/            # captured ATS JSON/XML fixtures
│       └── karriere_at/              # captured HTML fixtures
├── tests/
│   ├── test_ingest.py
│   ├── test_target_discovery.py
│   ├── test_fetcher.py
│   ├── test_extractors.py
│   ├── test_indexer.py
│   ├── test_similarity.py
│   └── test_reporter.py
├── research/
│   ├── AUSTRIA_JOB_LANDSCAPE.md      # subagent 1 report (verbatim)
│   ├── INVENTORY.md                  # jrf + stealth-core inventory
│   ├── PITFALLS.md                   # known anti-bot + parsing failures
│   └── raw/                          # raw extracts, HTML snapshots, robots.txt
├── .planning/
│   ├── PROJECT.md                    # this file
│   ├── ROADMAP.md                    # phase map
│   ├── STATE.md                      # session state (decisions, blockers)
│   ├── 01-RESEARCH.md                # research findings summary
│   └── 01-1-PLAN.md                  # iteration plan
└── data/
    └── austria_jobs.db               # live index (gitignored)
```

## Module contract (each independently runnable)

Every module in `austria_job_scout/modules/` is **one CLI subcommand**. Each can be run in isolation for testing:

| Subcommand | Input | Output | Side effects |
|------------|-------|--------|--------------|
| `ingest` | `--input file.pdf\|file.txt\|--role "Senior Rust Engineer"` | `ReferenceJob` JSON to stdout | none |
| `discover` | `--reference ref.json` or `--role ...` | `List[Target]` JSON to stdout | optional `--seed-index` |
| `fetch` | `--targets targets.json` | `List[RawResponse>` JSON to stdout | writes `fetch_log` (idempotent) |
| `extract` | `--raw raw.json` | `List[JobPosting>` JSON to stdout | none |
| `index` | `--jobs jobs.json` | count of rows written | writes `austria_jobs` (UNIQUE(url)) |
| `score` | `--reference ref.json --indexed-only` | `List[ScoredJob>` JSON to stdout | none |
| `report` | `--scored scored.json --format md\|json\|csv --out FILE` | written file | none |
| `pipeline` | combined: runs ingest→discover→fetch→extract→index→score→report | full report | all of the above |

This is the modularity guarantee: **each module is testable, replaceable, and progress-saveable**.
