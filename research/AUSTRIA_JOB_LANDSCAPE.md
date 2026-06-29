# Austria Job Discovery Landscape — Research Report

Compiled 2026-06-22. All citations inline. No fluff.

## 1. Aggregators / Meta-Search

### karriere.at — largest private AT board
- **Inventory**: 13.200+ active listings (homepage counter).
- **Search URL**: `https://www.karriere.at/jobs?q={keyword}&location={city}&page={n}` — `&page=2` works.
- **Job-detail URL**: `https://www.karriere.at/jobs/{numeric-id}` — 7–8 digit integer IDs (e.g. `10023736`).
- **robots.txt**: only blocks `BLEXBot`, `AhrefsBot`. Everyone else allowed. Declares `static/sitemaps/sitemap-jobs-https.xml` and `static/sitemaps/sitemap-firmen-bs-jobs-https.xml`.
- **Sitemap**: `/sitemap.xml` 404s; main index `/sitemaps-https.xml` also 404s from probe; per-robots sitemaps exist, `sitemap-jobs-https.xml` is large (gzipped).
- **Public API**: **NONE.** `/api/jobs` returns 404. Only third-party path: Apify `karriere-at-job-listings-scraper`.
- **Anti-bot**: **soft.** No Cloudflare/Akamai/DataDome headers observed. Vanilla `requests` works at polite rates.

### StepStone.at
- **Inventory**: 389 software-engineer jobs in Wien alone, 17 pages.
- **Search URL**: `https://www.stepstone.at/jobs/{slug}/in-{city-slug}`. Filter UI appends query params.
- **robots.txt**: **restrictive.** Blocks `/public-api/`, `/m/`, `/mobile/`, `/5/index.cfm`, `/skylight-backend`. Pattern-blocks `/jobs/*?*` while `Allow`-ing `/jobs/{keyword}?q=`.
- **Sitemap**: `/sitemap.xml` fetch error (likely CF interstitial).
- **Public API**: `/public-api/v1/job-applications/` is for posting only. Read access is **partner-only** (StepStone Partner Program).
- **Anti-bot**: **Cloudflare.** Stealth Playwright + residential proxy needed for >1k/day.

### willhaben.at/jobs
- **Inventory**: 15.312 jobs. Classifieds marketplace.
- **robots.txt**: **most aggressive.** Preamble: *"It is expressively forbidden to use spiders…"* Disallows `/jobs/webapi/`, `/restapi/`, `/jobs/suche*?*`, `/mob/`, `/pal/`.
- **Sitemap**: `https://cache.willhaben.at/jobs/service/public/sitemaps/sitemap.xml`. Returns error from single probe.
- **Anti-bot**: **strong.** Custom stack + DataDome-style fingerprinting. Sitemap feed is the only viable path.

### jobs.at
- 10.040+ jobs. URL: `/jobs?q=...&location=...`. JS-rendered pagination. No public API. **Anti-bot: medium.**

### jobbörse.at
- **Parked domain for sale.** Not a job board. Real government jobbörse is `jobboerse.gv.at`.

### kimeta.at
- 111.600 jobs, pure meta-search. No URL-based search; `/sitemap.xml` 404s. **Low priority.**

### hokify.at
- Mobile-first, ~55k employers. URL `/jobs/k/{keyword}`. Public API: none.

### Indeed.at
- `at.indeed.com`. CF content-signals block LLM crawlers. Anti-bot: **strong.**

### AMS / "alle jobs" (jobs.ams.at/public/emps/)
- **Inventory**: Largest AT job board — ~210k listings aggregated from AMS, WKO, BA, BZ.
- **URL**: `https://jobs.ams.at/public/emps/` — Angular SPA ("alle jobs - die Stellensuche des AMS").
- **robots.txt**: Allows `/public/emps/$` (landing page only). Disallows `/public/emps/` deeper paths for `*`. **But**: `LinkedInBot` gets full Allow. Real-world: polite scraping tolerated.
- **Backend API**: REST at `/public/emps/api/` — endpoints discovered from SPA JS bundle:
  - `/search` — main search (keyword, location, radius, education, working_time, sort)
  - `/frontend/predefined-searches` — homepage curated categories
  - `/open/v1/suggestions/jobtitle` — typeahead
  - `/open/v1/suggestions/location` — location typeahead
- **API auth**: **Session-gated.** Returns 401 `{"code":"UNAUTHORIZED"}` without valid `SM2_SESSION` cookie + `X-XSRF-TOKEN`. The SPA obtains these via initial page load + Angular HttpXsrfInterceptor. Direct curl/requests cannot search.
- **Job detail pages**: **Publicly accessible.** `https://jobs.ams.at/public/emps/jobs/uuid/{uuid}` returns server-side HTML (HTTP 200, no auth). UUIDs are v5 (namespace-based), e.g. `5c13494c-5c59-30f7-ba45-f0467410d2aa`.
- **Managed API wrappers**:
  - **Parse.bot** marketplace: 6 endpoints, free tier 100 calls/mo, $30/mo for 1k calls.
  - **Apify** `lexis-solutions/jobs-ams-at-scraper`: managed actor, pay-per-result.
- **Anti-bot**: **None.** Apache server, no Cloudflare/DataDome/Akamai. Session auth is the only gate.
- **Verdict**: **Tier-1 priority for coverage** but requires either (a) headless browser for SPA session init, (b) managed API (parse.bot/Apify), or (c) UUID harvesting from another source + direct detail page fetch. The job-detail pages are the path of least resistance for content extraction.

### Aggregator summary

| Source | Inventory | API | Sitemap | Anti-bot | Verdict |
|--------|-----------|-----|---------|----------|---------|
| karriere.at | 13.2k | ❌ | ✅ jobs sitemap | Soft | **Tier-1** via sitemap + HTML |
| StepStone.at | large | ❌ partner | partial | Cloudflare | Tier-1 with stealth |
| willhaben.at | 15.3k | ❌ | ✅ (cache CDN) | Strong | Tier-2; sitemap only |
| jobs.at | 10k | ❌ | unknown | Medium | Tier-2; needs JS |
| kimeta.at | 111.6k | ❌ | ❌ | Soft | Skip — meta |
| hokify.at | medium | ❌ | unknown | Soft | Skip — niche |
| Indeed.at | huge | ❌ | unknown | Cloudflare | Tier-3 |
| **AMS (jobs.ams.at)** | ~210k | session-gated | N/A | None | **Tier-1 coverage** (needs managed API or UUID detail pages) |
| Monster.at | small | ❌ | unknown | Akamai | Skip |

## 2. Common ATS used by Austrian employers

### Greenhouse — `boards.greenhouse.io`
- **Endpoint**: `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true` → JSON. JSONP via `?callback=`.
- **Payload**: `jobs[]` with `id`, `title`, `updated_at`, `location{name}`, `absolute_url`, `content` (HTML), `departments[]`, `offices[]`, `requisition_id`, `metadata[]`.
- **AT share**: G2 #1 EMEA ATS Winter 2025. Strong at tech scale-ups.

### Lever — `jobs.lever.co`
- **Endpoint**: `GET https://api.lever.co/v0/postings/{site}?mode=json`. EU instance: `https://api.eu.lever.co/v0/postings/{site}`.
- **Payload**: `id`, `text`, `categories{commitment,location,team,allLocations}`, `description`/`descriptionPlain`, `applyUrl`, `hostedUrl`, `createdAt`.

### Workday — `*.myworkdayjobs.com`
- **No public read API.** HTML renders server-side then hydrates with internal state blobs.
- **URL pattern**: `https://{tenant}.wd{N}.myworkdayjobs.com/{locale}/{site}` then `/job/{title-slug}_{requisition-id}`.
- **AT clients observed**: Cloudflight (wd103), Dedalus (wd3), Suse (wd3), Skechers (wd5), Kapsch, Austrian Post, Q8.
- **Anti-bot**: **Cloudflare + Turnstile.** Stealth browser (Playwright + JA3/JA4 match) required; mass ingestion fragile.

### SmartRecruiters — `jobs.smartrecruiters.com`
- **Endpoint**: `GET https://api.smartrecruiters.com/feed/publications` (single feed, paginated). Per-company: `…/v1/companies/{companyId}/postings`.

### Personio — `*.jobs.personio.de` / `*.jobs.personio.com`
- **Endpoint**: `GET https://{company}.jobs.personio.de/xml?language=en` → XML feed of all open positions.
- **AT share**: **highest in DACH Mittelstand** — 15k customers, DACH home turf. **Highest single ATS volume in AT.**

### SAP SuccessFactors — RMK Career Site Builder
- **Endpoint**: per-tenant XML feed. KBA 2428902: `https://<career_site_url>/xml` (sometimes `…/jobs/xml`, `…/rss`, `…/feed.xml`).
- **AT share**: high in large enterprises — Erste Group, Wiener Städtische, etc.

### Other AT-relevant ATS
- **Workable** — `GET https://www.workable.com/api/accounts/{subdomain}?details=true` → JSON.
- **Recruitee** — `GET https://{company}.recruitee.com/api/offers/` → JSON.
- **Softgarden** — `jobs.softgarden.de` public board.
- **d.vinci / BITE / dwp** — Austrian-developed; public board JSON varies.

### ATS summary

| ATS | Endpoint | Format | Auth | Anti-bot | AT share |
|-----|----------|--------|------|----------|----------|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{slug}/jobs` | JSON | None | Soft | High (tech) |
| Lever | `api.lever.co/v0/postings/{slug}?mode=json` | JSON | None | Soft | Medium |
| Workday | `*.myworkdayjobs.com` HTML | — | None | **Cloudflare + Turnstile** | High (enterprise) |
| SmartRecruiters | `api.smartrecruiters.com/feed/publications` | JSON | None | Soft | Medium |
| **Personio** | `{slug}.jobs.personio.de/xml` | **XML** | None | Soft | **Highest (Mittelstand)** |
| SuccessFactors | per-tenant `{career-url}/xml` | XML | None | Medium | High (enterprise) |
| Workable | `workable.com/api/accounts/{slug}?details=true` | JSON | None | Soft | Medium |
| Recruitee | `{slug}.recruitee.com/api/offers/` | JSON | None | Soft | Low |

## 3. Career-page discovery (CT logs + URL probe)

1. `https://{domain}/karriere` (AT/DE)
2. `https://{domain}/jobs`
3. `https://{domain}/careers`
4. `https://{domain}/stellenangebote`
5. `https://{domain}/jobs-und-karriere`
6. Subdomains: `careers.{domain}`, `karriere.{domain}`, `jobs.{domain}`, `talent.{domain}`
7. CMS: `/index.php?id=karriere` (Typo3 — common in AT public sector)

HEAD-probe 5×8 matrix, 30s soft timeout.

**CT log mining (crt.sh):**
```
GET https://crt.sh/?q=%25.{domain}&output=json
→ filter SAN DNS names for: *.careers.{domain}, careers.{domain},
   karriere.{domain}, jobs.{domain}, talent.{domain}
```

Note: crt.sh omits the apex; run both `q={domain}` AND `q=%.{domain}`.

**Sitemap discovery**: probe `/sitemap.xml` → scan `<urlset>` for `/karriere|jobs|careers|stellen`. Greenhouse has no sitemap — API is the only mechanism.

**robots.txt mining** for `Disallow: /karriere`, `/careers`, `/stellenangebote`, `/bewerbung`, `/bewerben`, `/mitarbeiter`.

## 4. opendata.host

**opendata.host is a company-registry service, NOT a job-posting source.** Compass-Verlag (since 1867).

- **Host**: `http://api.opendata.host/` (HTTP, not HTTPS).
- **Auth**: HTTP Basic — API key as username, password empty.
- **Endpoints**: `/vat-id/validate`, `/vat-id/find`, `/address/find`, `/registered-companies/find`.
- **Use**: enumerate Austrian companies by name → UID → discover all legal entities in the group → run career discovery on each. **Does not expose domain field** — for domain→UID use `registered-companies/find` and correlate, or canonical Austrian Firmenbuch (`justiz.gv.at`).

## 5. Anti-bot reality check ("three pillars")

| Source | CDN/WAF | Bot protection | Stealth needed? |
|--------|---------|----------------|-----------------|
| **karriere.at** | none observed | minimal | **No** |
| **StepStone.at** | Cloudflare | Cloudflare Bot Mgmt | **Yes (mid)** |
| **willhaben.at** | custom + likely DataDome | fingerprint + JS challenge | **Yes (high)** |
| **jobs.at** | unknown | JS-render only | **Yes (mid)** |
| **Indeed.at** | Cloudflare | Managed Challenge + content signals | **Yes (high)** |
| **myworkdayjobs.com** | Cloudflare | Turnstile + advanced fingerprint | **Yes (very high)** |
| **jobs.personio.de** | none observed | None for /xml | **No** |
| **boards-api.greenhouse.io** | none | None | **No** |
| **api.lever.co** | none | None | **No** |
| **api.smartrecruiters.com** | none | None | **No** |
| **careers.successfactors…** | varies | per-tenant | **Maybe** |

### Three pillars verdict

- **karriere.at: NO.** No `cf-*`, `akamai-*`, or `x-datadome` headers. Public sitemap serves directly. **Easiest major AT source.**
- **StepStone.at: YES (Cloudflare).** Expect 403 after 5–10 default-UA requests.

### Recommendations by source
- **Greenhouse / Lever / SmartRecruiters / Personio XML / SuccessFactors XML**: pure HTTP. **Zero stealth.**
- **AMS (jobs.ams.at): NO WAF, but session-gated API.** No Cloudflare/DataDome. Backend REST API requires SM2_SESSION + XSRF token (Angular SPA init). Job-detail UUID pages are public HTML. Use parse.bot/Apify for search, or direct UUID fetch for details.
- **karriere.at**: polite HTTP + rate-limit. Sitemap is highest ROI.
- **jobs.at / kimeta / hokify**: headless browser only, low volume.
- **willhaben.at / StepStone / Indeed**: stealth browser, cap per-IP, expect <50% success.
- **Workday**: budget 3–5x engineering. Consider TheirStack, JobsPipe, or Fantastic.jobs aggregators.
