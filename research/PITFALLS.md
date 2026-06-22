# Known Pitfalls & Anti-Patterns to Avoid (2026-06-22)

Compiled from the existing jrf + stealth-core codebase, the Austrian job landscape research, and
the user's standing preferences (silent work, modular code, stealth-core reuse).

## ⛔ PILLAR 0 — RESIDENTIAL IP PROTECTION (PARAMOUNT, supersedes everything below)

The user's residential home IP is the scarcest resource in this project. Once burned — by Cloudflare
flagging, by DataDome flagging, or by any ISP-level abuse report — the IP cannot be used for normal
browsing without captchas on every site, possible ISP throttling, and possible TOS warnings.

**This constraint outranks throughput, coverage, and convenience.** Every other pillar in this doc,
and every default in `config.py`, is calibrated to keep the residential IP clean.

Concrete rules — enforced in `austria_job_scout/config.py`:

1. **`AGGRESSIVE_MODE` is opt-in.** Default is conservative. To enable aggressive fetches the user
   must set `AUSTRIA_JOB_SCOUT_AGGRESSIVE=1` explicitly. The first-run wizard must ask "Are you on
   a residential IP?" and refuse to proceed until the user answers.

2. **Daily budget cap = 50 requests/day** (residential) / **10/day** for WAF sites / **500/day**
   in aggressive mode. The cap is enforced by counting every entry in `fetch_log` per UTC day.

3. **Long-tail delays**, not uniform. Default `5–25s` per request, plus a 15% chance of a
   `60–180s` long pause. Uniform `time.sleep(2)` was the bug that bit `company-quickcheck` in 2026-06
   (5 fixed sleeps at 0.3/0.5/1.0/1.5/2.0s — fingerprintable). Never use a fixed sleep on the
   network path. Never.

4. **WAF-protected sites are BLOCKED from residential IP.** `stepstone.at`, `indeed.com`,
   `myworkdayjobs.com`, `willhaben.at`, `jobs.at` raise `CFProtectedSiteError` and refuse to fetch
   in residential mode. To hit them, the user must (a) enable `AGGRESSIVE_MODE` AND (b) have a
   residential proxy configured. No exceptions. (See Pillar 9 for the longer explanation.)

5. **Per-domain circuit breaker.** `CIRCUIT_BREAKER_THRESHOLD=3` consecutive failures →
   24h cool-off. If karriere.at 403s us 3 times in a row, we don't try again until tomorrow.
   This prevents the "try harder → burn the IP" failure mode.

6. **Navigation noise mandatory.** Always `GET /` first, `GET /jobs` second, only then `GET /jobs/123`.
   Never deep-link to a job page on the first request. The fetcher must synthesise a `Referer`
   chain (`""` → `https://{host}/` → `https://{host}/jobs` → target) and an `Accept-Language`
   that matches the IP's apparent locale (de-DE-AT for AT sites).

7. **User-Agent = real browser, no headless signature.** Use `curl_cffi` with
   `impersonate="chrome120"` so TLS JA3 + HTTP/2 + browser headers all match. Never send a
   custom Python `User-Agent`. The fingerprint must be indistinguishable from a real Chrome
   on the same IP.

8. **Audit grep before every commit.** Search for fixed `time.sleep(...)` in any module that
   imports `requests` or `curl_cffi`. If found, replace with `time.sleep(random.uniform(...))`.
   Same for any `requests.get(...)` without `impersonate=` — block the PR.

9. **Honest failure reporting.** If the daily budget is exhausted, the fetcher returns
   `DailyBudgetExhausted` (not a silent 0 results). If a circuit breaker trips, the report says
   "skipped: 3 consecutive failures, cooling off until 2026-06-23 14:32 UTC" — never pretend
   we tried.

## Pillar 0b — Discrimination-before-fetch (PARAMOUNT, second constraint)

The user explicitly restated (2026-06-22): **don't fetch hundreds of jobs all at once. Devise some
means of discrimination.** This is the second half of the residential-IP rule — even within the
budget, be selective about WHAT we spend the budget on.

Concrete rules — enforced in `austria_job_scout/config.py`:

1. **Hard cap per run.** `MAX_FETCH_PER_RUN=25` (residential) / `200` (aggressive). A single
   `pipeline` invocation MUST refuse to schedule more fetches than this — split across days.

2. **Pre-filter by source tier.** Fetch in priority order (lower number first):
   - Tier 1 (priority 10): ATS JSON/XML — Greenhouse, Lever, SmartRecruiters, Workable, Recruitee,
     Personio. Zero stealth, zero detection risk. These go first.
   - Tier 2 (priority 20-25): Aggregator sitemaps + soft-anti-bot search (karriere.at,
     AMS OGD). Server-side relevance ordering; honor `?page=1&limit=N`.
   - Tier 3 (priority 30): Direct career pages of known companies, RSS feeds.
   - Tier 4 (priority 90, BLOCKED residential): WAF sites (Workday, StepStone, Indeed, willhaben, jobs.at).
   Within a tier, sort by predicted relevance to the reference.

3. **Top-N per source.** `TOP_N_PER_SOURCE=10` (residential). After a source returns its ranked
   list (e.g. karriere.at returns 287 hits for "Senior Rust Engineer"), only FETCH the top 10.
   The remaining 277 are saved to the **wishlist** table with `wishlisted_at` timestamp; the
   user can re-run the pipeline the next day to pull more.

4. **Aggregator pagination cap.** `MAX_AGGREGATOR_PAGES=1` (residential). Don't paginate to
   page 2/3 even when budget allows — the long tail of aggregator results is diminishing
   returns for our budget. Re-rank locally with our hybrid scorer instead.

5. **Post-parse filter.** `MIN_SKILL_OVERLAP_FOR_INDEX=0.2`. After parsing a JobPosting, compute
   the Jaccard overlap between reference skills and posting skills. If < 20%, write to
   `austria_jobs` with `status='skipped_low_overlap'` so we know it exists but don't burn
   similarity budget on it. This catches the case where karriere.at returned 287 hits but
   only 30 actually mention any of the reference's skills.

6. **Wishlist, not drop.** If `pipeline` would need more than `MAX_FETCH_PER_RUN` fetches, save
   the remainder to a `wishlist` table with `(reference_id, url, predicted_relevance,
   wishlisted_at)`. Next run: dedupe against the wishlist, fetch a new tranche of
   `MAX_FETCH_PER_RUN` items, mark fetched ones as `wishlist_status='fetched'`. Never lose a
   target silently.

7. **Company-prioritization via opendata.host.** Before adding a company to the candidate set,
   check opendata.host status. Skip companies where `opendata_status='geloescht'` (deleted) or
   `'insolvent'`. Only include companies that have at least one ATS endpoint in our known list
   (Greenhouse/Lever/Personio/etc.) OR a `/karriere` path that returns 200.

8. **Predicted-relevance gate.** Each candidate target gets a `predicted_relevance` score
   before fetch (0.0-1.0, heuristic from: company industry match, role-token overlap with
   reference title, location overlap). Skip targets with `predicted_relevance < 0.15`.
   Saves budget on companies that are clearly wrong (banking job for "Rust engineer" ref).

9. **Honest reporting.** If discrimination reduces the candidate set from 287 to 17, the report
   says: "287 candidates from karriere.at; ranked top 17 by relevance; skipped 270 to
   wishlist for next run; fetched 17; indexed 12 (5 below MIN_SKILL_OVERLAP_FOR_INDEX)."
   Never "we found 0 results" when there were actually 270 candidates on the wishlist.

## Pillar 1: Don't crawl the same URL twice

**Pitfall:** calling `requests.get()` repeatedly for the same job URL wastes rate budget and
risks detection. Fix is **mandatory**: a `fetch_log` SQLite table with `url_hash PRIMARY KEY`.

```sql
CREATE TABLE fetch_log (
    url_hash TEXT PRIMARY KEY,        -- sha256(url)
    url TEXT NOT NULL,
    first_checked INTEGER NOT NULL,
    last_checked INTEGER NOT NULL,
    last_status INTEGER,              -- HTTP status of last fetch
    last_etag TEXT,
    last_modified TEXT,
    last_changed_at INTEGER,          -- when content actually changed
    fetch_count INTEGER DEFAULT 1,
    notes TEXT
);
CREATE INDEX fetch_log_last_checked ON fetch_log(last_checked);
```

The Fetcher must check `fetch_log` before every network call. If `last_checked < 24h ago AND last_status = 200`,
return the cached RawResponse without going to the network.

## Pillar 2: Don't fake ATS-specific URLs

**Pitfall:** hard-coding `boards.greenhouse.io/<token>` for every "tech" company. Wrong — only some
tech companies use Greenhouse. The classifier must look at:
- The response `Server` header (Greenhouse: empty, Lever: empty, Workday: Workday)
- The HTML `<meta name="...">` and `<link>` tags
- The URL pattern itself

The classifier output is the ATS fingerprint (`greenhouse`, `lever`, `workday`, `personio`,
`successfactors`, `karriere_at`, `stepstone_at`, `jobs_at`, `unknown_html`).

## Pillar 3: Don't scrape the company name from the URL slug

**Pitfall:** assuming the slug in `boards.greenhouse.io/acme-co` is the company name. Sometimes
the slug is `acme-careers` or `acmegroup`. Always extract the company from the JSON response.

## Pillar 4: opendata.host is HTTP, not HTTPS

**Pitfall:** calling `https://api.opendata.host/...` — gets refused. Correct: `http://api.opendata.host/`.
Carry the API key as HTTP Basic username, password empty.

## Pillar 5: jrf's job_postings UNIQUE is on (job_id, source_url)

**Pitfall:** trying to insert the same `source_url` with a different `job_id` — silently fails
or silently overwrites depending on `OR IGNORE` flag. For our dedupe we want a stricter key:
**just `source_url`** is the canonical dedupe. Two ATS feeds publishing the same listing → one row.

## Pillar 6: karriere.at IDs are numeric, no slug

**Pitfall:** trying to extract a job_id from `/jobs/senior-engineer-wien-12345` — karriere.at URLs
are `/jobs/12345` (just digits). The `_extract_job_id()` function in jrf already handles some
patterns; we extend it with regex `/jobs/(\d{6,})`.

## Pillar 7: Personio XML endpoint sometimes needs language param

**Pitfall:** calling `/xml` returns German by default for `*.jobs.personio.de`. To get English,
call `/xml?language=en`. Always probe both — store whichever is non-empty.

## Pillar 8: karriere.at sitemap is gzipped and large

**Pitfall:** `curl https://www.karriere.at/static/sitemaps/sitemap-jobs-https.xml` hangs at
30s+ on a single connection. Fix: stream with `requests.get(..., stream=True)` + `iter_content`,
or use `curl --compressed -o -`. Document in extractor.

## Pillar 9: Workday & Indeed → defer

**Pitfall:** trying to fetch `*.myworkdayjobs.com` from this RPi → immediate 403/turnstile.
Don't waste effort on v1. Mark as DEFERRED in the target list.

## Pillar 10: Don't conflate "similar" with "matching"

**Pitfall:** scoring purely by exact keyword overlap → loses semantic matches ("Rust" vs "memory-safe
systems programming"). Use **hybrid**: 50% semantic embedding cosine + 30% FTS5 BM25 + 20%
skill-set Jaccard. Default weights in v1, expose as CLI flags.

## Pillar 11: Tokenizers pin in jrf venv

**Pitfall:** running `pip install -r requirements.txt` naively pulls `transformers>=4.30` which
demands `tokenizers<=0.23.0`. The current installed `0.23.1` breaks 6 tests. Workaround for v1:
set `rerank_top_k=0` in `HybridSearch.search()` and skip the cross-encoder path entirely. Document
in `requirements.txt`.

## Pillar 12: stealth-core binary on this RPi

**Pitfall:** the pre-built `~/.local/bin/stealth-core` needs GLIBC 2.39; RPi has 2.36.
The `cargo build --release` rebuild takes 20+ min on Pi and may fail on the dep tree. v1:
do not call the binary. Use `jrf.scripts.stealth_fetch` instead. When the binary is fixed,
`Fetcher` is the single swap point.

## Pillar 13: Sitemap paths are not at /sitemap.xml

**Pitfall:** assuming every site serves `/sitemap.xml`. karriere.at serves it at
`/static/sitemaps/sitemap-jobs-https.xml` (declared in robots.txt). Personio has no sitemap
(`/xml` is the API). Greenhouse has no sitemap. The fetcher must read `robots.txt` first and
look for `Sitemap:` directives.

## Pillar 14: Greenfield skills should not modify other skills' DBs

**Pitfall:** creating new tables in `~/job-research-framework/data/patterns.db` mutates an
external skill. The new skill gets its **own DB** at `~/austria-job-scout/data/austria_jobs.db`.
Cross-skill queries happen via a read-only `ATTACH` if ever needed.

## Pillar 15: Report columns must be human-readable

**Pitfall:** dumping raw cosine scores. The report shows:
- **Match** (0-100%) — combined score, the headline number
- **Title** — verbatim from the posting
- **Company** — canonical name
- **Location** — city, AT-postal-prefix
- **Salary** — if listed (parse `<span class="salary">` etc., else "n/a")
- **Type** — full-time / part-time / contract / internship
- **ATS** — which platform posted it
- **Skills matched** — list of skills from the reference that appear in the posting
- **Link** — the canonical apply URL (NOT the scraper's source URL)
- **First seen** — when we first indexed this posting
- **Last checked** — for staleness
