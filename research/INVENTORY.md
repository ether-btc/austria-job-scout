# Inventory — Reusable Building Blocks (2026-06-22)

This is what already exists on disk that the new `austria-job-scout` skill can import as a library.
Every entry below was read from the actual source, not the README.

---

## A. job-research-framework (jrf) — Python, on USB

**Path:** `/media/hermes-pi/f3fd4a1d-0ea7-4afb-b8e7-72349bac728c/hermes/projects/job-research-framework/`
**Venv:** `./venv/bin/python` ✅ present, working.
**Smoke test:** 7/7 passing in 2.44s.
**Full test suite:** 21/27 passing — 6 failures are all upstream `tokenizers>=0.22.0,<=0.23.0` env mismatch + a stale `skills.cross_encoder` import. None block the new skill.

### A.1 SQLite schema (`data/patterns.db`, 92 KB, currently 0 rows)

```sql
CREATE TABLE job_postings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    job_type TEXT,
    application_email TEXT,
    application_url TEXT,
    raw_html TEXT,
    extracted_at INTEGER,
    source_url TEXT NOT NULL,
    source_domain TEXT NOT NULL,
    last_checked INTEGER,
    status TEXT DEFAULT 'active',
    UNIQUE(job_id, source_url)
);

CREATE TABLE job_chunks (
    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id INTEGER NOT NULL,
    chunk_type TEXT NOT NULL,
    content TEXT NOT NULL,
    source_url TEXT,
    source_region TEXT,
    index_in_posting INTEGER,
    created_at INTEGER,
    FOREIGN KEY (posting_id) REFERENCES job_postings(id) ON DELETE CASCADE,
    CHECK (chunk_type IN ('description', 'requirements', 'compensation', 'metadata'))
);

-- + job_chunks_embeddings, job_chunks_fts (FTS5), embedding_errors,
--   strategy_preferences, strategy_rankings, detection_events, db_metadata

INDEXES:
  idx_chunks_posting (job_chunks.posting_id)
  idx_chunks_type    (job_chunks.chunk_type)
  idx_postings_job_id (job_postings.job_id)
  idx_postings_source_url (job_postings.source_url)
  idx_postings_status  (job_postings.status)
  idx_postings_company (job_postings.company)
```

**Reuse:** the new skill can reuse `job_postings` + `job_chunks` directly. We only need to **add** two new tables in a **separate** DB (so we don't mutate jrf's schema). The new DB also gets `fetch_log` for the dedupe index.

### A.2 Public function surface (verified)

| Module | Symbol | Use for |
|--------|--------|---------|
| `skills.job_scraper.JobScraper` | `scrape_job_url(url, company, source_domain) → Optional[Dict]` | One-shot fetch+parse+store of a single job URL |
| `skills.job_scraper` (module-level) | `scrape_job_url(...)`, `store_chunks(posting_id, chunks, db_path)` | Stateless helper |
| `scripts.stealth_fetch` | `stealth_fetch(url, timeout=30, impersonate=None, verify=True, **kwargs) → (text, status, error)` | TLS-impersonated HTTP |
| `scripts.ct_log_miner` | `discover_career_subdomains(domain, delay=5, max_pages=5)` | crt.sh mining for career-related subdomains |
| `scripts.ct_log_miner` | `discover_company_career_urls(company_name, domain=None, ...)` | Company-name to career URL discovery |
| `scripts.strategy_engine` | `select_technique(site_domain, db_path) → (level, technique)` | Pick stealth level per domain |
| `scripts.strategy_engine` | `record_result(site_domain, technique, success, ...)` | Record outcome for adaptive learning |
| `skills.vector_search.VectorSearch` | `semantic_search(embedding, min_score=0.5, limit=10)` | Cosine similarity over chunk embeddings |
| `skills.vector_search.KeywordSearch` | `keyword_search(query, limit=10)` | FTS5 BM25 |
| `skills.vector_search.HybridSearch` | `search(query, embedding_generator, min_score=0.3, limit=10, chunk_types=None, rerank_top_k=0)` | Combined semantic + keyword |
| `skills.rag_pipeline.RAGPipeline` | `query(query, min_score=0.3, filters=None)` | Full RAG with reranking + query expansion |
| `skills.embeddings.EmbeddingGenerator` | `generate_embedding(text)`, `save_embedding(chunk_id, emb)` | Embedding model wrapper |

### A.3 Pitfalls observed in jrf

- **transformers pin conflict**: `tokenizers==0.23.1` is too new; requires `>=0.22.0,<=0.23.0`. This breaks 6 tests in cross-encoder + rag-pipeline. Not on the critical path for v1 (we use cosine only).
- **`skills.cross_encoder` import**: missing from `skills/__init__.py` exports → 2 stale tests. Cosmetic.
- **`stealth_fetch` is good** — confirmed working: `curl_cffi` is installed; the function falls back to vanilla curl if TLS impersonation fails.

---

## B. stealth-core — Rust binary, on USB

**Path:** `/home/hermes-pi/projects/stealth-core/` (project root).
**Binary:** `/home/hermes-pi/.local/bin/stealth-core`.
**Status on RPi:** ❌ **binary broken — `GLIBC_2.39 not found` (RPi has 2.36).** Use Python `curl_cffi` (jrf) instead until rebuilt.

### B.1 REST endpoints (verified from `src/api/mod.rs`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Engine health, uptime, proxy summary |
| GET | `/metrics` | Prometheus metrics (text/plain) |
| GET | `/proxy/health` | Per-proxy health |
| GET | `/cache/stats` | Cache statistics |
| GET | `/fetch?url=...` | Stealth fetch |
| POST | `/fetch` | Batch fetch (JSON body) |

### B.2 Reuse strategy

- **Do not call stealth-core from v1** — the binary is broken on this RPi.
- All HTTP needs go through `jrf.scripts.stealth_fetch.stealth_fetch()`.
- When stealth-core is rebuilt (or replaced by Playwright sidecar per `mlops/stealth-core-platform`), the fetcher module can be swapped behind the same `Fetcher.fetch(target) → RawResponse` interface. The cache key (`CacheKey::new(url, method, headers)` from `src/cache/mod.rs:46`) is the same SHA-2 hash we'll use.

---

## C. firmen-quickcheck — Python, on disk

**Path:** `/home/hermes-pi/company-quickcheck/`
**Purpose:** Austrian company status checker via `opendata.host`.

### C.1 What's useful for v1

- `autonomous_batch.py` (16 KB) — already has company-status lookup patterns.
- Uses opendata.host API for `registered-companies/find` — exactly the company→UID step we need.
- venv separate from jrf: `~/company-quickcheck/venv`.

### C.2 Reuse strategy

- **Import as library**: `from autonomous_batch import OpenDataHostClient` to look up the UID and any registered domains.
- Will extend the skill to **also surface the company registration status** in the report ("Austrian Post AG — active, FN 12345x").

---

## D. ocr-and-documents skill — `~/.hermes/skills/productivity/ocr-and-documents/`

For **PDF input** handling. Two-tier recommendation:

| Scenario | Tool |
|----------|------|
| Text-based PDF (digital, not scanned) | `pymupdf` + `pymupdf4llm` (~25 MB) |
| Scanned PDF | `marker-pdf` (~5 GB, PyTorch + models) |

For v1 we ship `pymupdf` only; the user can opt into `marker-pdf` for scans.

---

## E. Existing skills used as libraries

| Skill | Path | What we import |
|-------|------|---------------|
| `firmen-quickcheck` | `~/company-quickcheck/` | `OpenDataHostClient` |
| `job-research-framework` | USB (above) | `JobScraper`, `stealth_fetch`, `ct_log_miner`, `HybridSearch`, `EmbeddingGenerator` |
| `ocr-and-documents` | `~/.hermes/skills/productivity/ocr-and-documents/` | `extract_pymupdf.py` for PDF→text |
| `anti-bot-three-pillars` | `~/.hermes/skills/anti-bot-three-pillars/` | Mini-audit + 3-pillar checklist per source |
| `cross-system-synergy-design` | `~/.hermes/skills/cross-system-synergy-design/` | Loose-coupling rules for adapter sidecar |

---

## F. What's NOT reusable (must build)

| Capability | Why new |
|------------|---------|
| Input parsing of role-name → ReferenceJob | Not in jrf |
| Target discovery (CT logs + HEAD probes + ATS classifier) | jrf has `ct_log_miner` but no full discovery funnel |
| ATS-specific extractors (Greenhouse JSON, Personio XML, etc.) | jrf has generic HTML only |
| Skill-set Jaccard similarity | jrf does hybrid semantic + keyword only |
| Reporting (Markdown / JSON / CSV with score breakdown) | jrf has `export_results` for raw results |
| Per-URL dedupe hash | jrf has `UNIQUE(job_id, source_url)` but not hash-based fetch log |

---

## G. Hard blockers discovered

1. **stealth-core binary is broken on this RPi** (GLIBC 2.39 vs 2.36). Workaround: use Python `curl_cffi` via jrf. The new skill's `Fetcher` interface is designed so this can be swapped once stealth-core is rebuilt.
2. **jrf's transformers pin** causes 6 RAG tests to fail on import. Workaround: skip cross-encoder in v1; use HybridSearch with `rerank_top_k=0`.
3. **karriere.at sitemap main file is large** (gzipped) and timed out a single-connection probe. Workaround: streaming XML consumer with retries.
4. **Indeed.at / Workday** are Cloudflare-protected with Turnstile. Deferred to v2.
