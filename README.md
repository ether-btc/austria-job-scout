# austria-job-scout

Find similar Austrian job postings given a reference job (PDF, text, or role name).

## What It Does

Given a job posting (PDF file, text description, or just a role name), this tool:

1. **Extracts key info** (title, skills, location, seniority, salary)
2. **Discovers relevant sources** (ATS career pages, job aggregators)
3. **Fetches job postings** with stealth protection (residential IP-friendly)
4. **Parses structured data** (Workday, Greenhouse, Lever, SmartRecruiters, karriere.at, jobs.at)
5. **Computes similarity** (TF-IDF or sentence-transformers embeddings)
6. **Generates reports** (Markdown, JSON, CSV)

## Anti-Bot Design (Stealth Core)

Implements the **three-pillar anti-bot framework** to avoid detection and IP bans:

### Pillar 0: Residential IP Protection

- **Default conservative mode** (AGGRESSIVE_MODE=0): 50 req/day (residential), 10 req/day (WAF)
- **Budget tracking** persists across runs in SQLite
- **Random delays**: 5-25s sleep before each fetch + 15% chance of 60-180s pause
- **Circuit breaker**: 3 consecutive failures trigger 24h cool-off
- **WAF blocklist**: stepstone.at, indeed.com, myworkdayjobs.com, willhaben.at, jobs.at blocked unless aggressive mode enabled

### Pillar 0b: Discrimination-before-Fetch

- **Strict caps** (residential mode): MAX_FETCH_PER_RUN=25, TOP_N_PER_SOURCE=10
- **Source tiering**:
  - Tier 1: ATS JSON feeds (highest signal)
  - Tier 2: Aggregator search pages
  - Tier 3: Career path enumeration
  - Tier 4: WAF-protected sites (blocked in conservative mode)
- **Remainder handling**: Excess targets saved to wishlist (not dropped silently)

### Pillar 0c: Deduplication (via SQLite)

- URL-based deduplication prevents re-fetching same postings
- Database persists fetched URLs and metadata
- Fingerprinting for near-duplicate detection

## Architecture

```
austria_job_scout/
├── modules/
│   ├── config.py          # Settings & env vars
│   ├── db.py              # SQLite persistence
│   ├── ingest.py          # Reference job extraction (PDF/text/role)
│   ├── fetcher.py         # Stealth HTTP fetching with budget guard
│   ├── target_discovery.py # Probes orchestrator
│   ├── indexer.py         # Vector embeddings & similarity
│   ├── similarity.py      # Match scoring
│   ├── reporter.py        # Report generation
│   └── pipeline.py        # End-to-end orchestrator
├── probes/
│   ├── ats_classifier.py  # Detect ATS type from URL
│   ├── aggregator_search.py # Build aggregator URLs (karriere.at, jobs.at)
│   └── career_paths.py    # Enumerate company career paths
├── extractors/
│   ├── ats_extractor.py   # Workday, Greenhouse, Lever, SmartRecruiters
│   └── aggregator_extractor.py # karriere.at, jobs.at, willhaben.at
├── cli.py                 # Command-line interface
└── schema.sql             # SQLite schema
```

## Installation

```bash
# Base dependencies
pip install -e .

# Optional: ML embeddings (sentence-transformers)
pip install -e ".[embeddings]"

# Optional: Stealth (curl_cffi)
pip install -e ".[stealth]"

# Dev dependencies (pytest)
pip install -e ".[dev]"
```

## Quick Start

```bash
# From a PDF job posting
austria-job-scout ~/Downloads/senior-engineer-job.pdf

# From text description
austria-job-scout --role "Senior Rust Engineer" --location "Wien" --skills Rust,PostgreSQL

# From a text file
austria-job-scout ~/job_description.txt

# With custom output directory
austria-job-scout --output-dir ~/job-reports my-job.pdf
```

## CLI Usage

```
Usage: austria-job-scout [OPTIONS] REFERENCE_JOB

  Find similar Austrian job postings.

Arguments:
  REFERENCE_JOB  Path to PDF/docx/text file or role name string

Options:
  --output-dir PATH       Directory to save reports [default: current dir]
  --use-ml               Use sentence-transformers embeddings [default: TF-IDF]
  --max-fetches INTEGER  Max URLs to fetch [default: budget-based]
  --min-similarity FLOAT Minimum similarity score 0-1 [default: 0.5]
  --top-k INTEGER        Number of matches to report [default: 10]
  --report-format TEXT   Report format: text, json, csv, all [default: text]
  --filter-location TEXT Filter by location
  --filter-seniority TEXT Filter by seniority level
  --filter-remote / --no-filter-remote
                         Filter remote jobs
  --filter-salary-min INTEGER  Minimum salary (EUR)
  --help                 Show help
```

## Environment Variables

```bash
# Database path
export AUSTRIA_JOB_SCOUT_DB="~/.austria-jobs.db"

# Residential IP mode (default: conservative)
export AGGRESSIVE_MODE=0  # 0=conservative, 1=aggressive

# Daily budget (residential mode)
export DAILY_BUDGET_RESIDENTIAL=50

# Max fetches per run
export MAX_FETCH_PER_RUN=25
```

## Programmatic Usage

```python
from austria_job_scout.modules.pipeline import run_pipeline

results = run_pipeline(
    reference_job="Senior Rust Engineer",
    output_dir="~/job-reports",
    use_ml=False,  # TF-IDF (faster, no GPU)
    max_fetches=25,
    min_similarity=0.5,
    top_k=10,
    report_format="all",
    filters={
        "location": "Wien",
        "remote": False,
        "salary_min": 60000,
    },
)

print(f"Found {len(results['matches'])} matches")
print(f"Reports: {results['reports']}")
```

## Outputs

### Text Report (Markdown)

```markdown
# Similar Jobs Report

**Reference Job:** Senior Rust Engineer
**Matches Found:** 7

## Summary
- **Average Overall Score:** 72.3%
- **Average Title Similarity:** 68.5%
- **Average Skills Overlap:** 75.0%

## Similar Jobs

### 1. Senior Rust Backend Engineer
**Company:** Tech GmbH
**Location:** Wien
**URL:** https://jobs.at/jobs/12345

**Similarity Scores:**
- Overall: 85.2%
- Title: 90.0%
- Description: 78.5%
- Skills: 80.0%

**Skills:** Rust, PostgreSQL, Redis, Kubernetes
```

### JSON Report

```json
{
  "reference": {
    "title": "Senior Rust Engineer",
    "url": "N/A"
  },
  "summary": {
    "total_matches": 7,
    "avg_overall_score": 0.723
  },
  "matches": [...]
}
```

### CSV Report

```csv
Rank,Title,Company,Location,URL,Overall Score,Title Similarity,Skills
1,Senior Rust Backend Engineer,Tech GmbH,Wien,https://...,0.8520,0.9000,0.8000
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test suite
pytest tests/test_fetcher.py -v

# Run with coverage
pytest --cov=austria_job_scout tests/
```

## Database Schema

```sql
-- Schema: schema.sql
CREATE TABLE IF NOT EXISTS fetched_urls (
    url TEXT PRIMARY KEY,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    content_hash TEXT,
    ats_type TEXT,
    source_kind TEXT
);

CREATE TABLE IF NOT EXISTS wishlist (
    url TEXT PRIMARY KEY,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS budget_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE,
    fetches_count INTEGER,
    failures_count INTEGER,
    UNIQUE(date)
);
```

## Stealth Configuration

### Conservative Mode (Default)

- 50 req/day residential
- 10 req/day WAF (blocklist enforced)
- 5-25s delays + 15% chance of 60-180s pause
- 3-failure cool-off
- No WAF sites: stepstone.at, indeed.com, myworkdayjobs.com, willhaben.at, jobs.at

### Aggressive Mode

Set `AGGRESSIVE_MODE=1` to:
- Disable residential budget
- Fetch from WAF sites
- Reduce delays to 2-5s
- Disable cool-off

⚠️ **Warning**: Aggressive mode increases ban risk significantly.

## Deduplication

- URL-based deduplication prevents re-fetching
- Content hashing detects near-duplicates
- Wishlist saves unfetched targets for later runs

## Supported Sources

### ATS (Tier 1 - JSON feeds)
- Workday (myworkdayjobs.com)
- Greenhouse (greenhouse.io)
- Lever (lever.co)
- SmartRecruiters (smartrecruiters.com)

### Aggregators (Tier 2 - HTML parsing)
- karriere.at
- jobs.at
- willhaben.at (career pages only in conservative mode)

### Career Paths (Tier 3)
- Company career pages (/jobs, /careers, /openings)

## Modules

### ingest.py

Extracts structured data from:
- PDF files (PyMuPDF)
- DOCX files (python-docx)
- Text files
- Role name strings

Outputs: ReferenceJob dataclass with title, skills, location, etc.

### fetcher.py

Stealth HTTP client with:
- Budget tracking (SQLite)
- Random delays
- Circuit breaker
- Residential IP guard

### target_discovery.py

Orchestrates probes to discover job URLs:
- ATS classifier (detect ATS type)
- Aggregator search (build URLs)
- Career path enumeration

### extractor/*.py

Parses structured data from:
- ATS JSON-LD + custom parsers
- Aggregator HTML listings

### indexer.py

Computes embeddings:
- TF-IDF (default, fast)
- sentence-transformers (optional, accurate)

### similarity.py

Computes match scores:
- Embedding similarity (primary)
- Title similarity
- Skills overlap
- Location match
- Seniority match
- Salary compatibility

### reporter.py

Generates reports:
- Markdown (human-readable)
- JSON (machine-readable)
- CSV (spreadsheet-friendly)

### pipeline.py

End-to-end orchestrator:
1. Ingest reference job
2. Discover targets
3. Fetch targets (stealth)
4. Extract job details
5. Index jobs
6. Find similar jobs
7. Generate reports

## Limitations

- **Conservative mode**: Limited to 25 fetches per run, 50 per day
- **WAF sites**: Blocked in conservative mode (stepstone.at, indeed.com, etc.)
- **Language**: Optimized for German/English postings
- **Geography**: Austria-specific (karriere.at, jobs.at, etc.)

## Contributing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Format code
black austria_job_scout/

# Lint
ruff check austria_job_scout/
```

## License

MIT