-- austria_job_scout canonical schema
-- Idempotent: every CREATE uses IF NOT EXISTS. Safe to re-run.
--
-- The DB is the single source of truth. Every module consults it before doing
-- anything destructive on the network or filesystem.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ============================================================================
-- schema version (lets future migrations upgrade in place)
-- ============================================================================
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL,
    notes TEXT
);

INSERT OR IGNORE INTO schema_version (version, applied_at, notes)
VALUES (1, strftime('%s','now'), 'initial schema — austria-job-scout v0.1');

-- ============================================================================
-- reference_jobs — input from the user (PDF / TXT / role-name)
-- ============================================================================
CREATE TABLE IF NOT EXISTS reference_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      INTEGER NOT NULL,
    source          TEXT NOT NULL,        -- 'pdf' | 'txt' | 'role_name'
    source_path     TEXT,                 -- absolute path or NULL for role_name
    raw_text        TEXT NOT NULL,        -- the user-supplied text (verbatim)
    title           TEXT,                 -- parsed (best-effort)
    company         TEXT,                 -- parsed (best-effort)
    location        TEXT,                 -- parsed (best-effort)
    language        TEXT,                 -- 'de' | 'en' | 'mixed' | 'unknown'
    skills_json     TEXT,                 -- JSON array of detected skills
    embedding_json  TEXT,                 -- 1536-dim vector as JSON string (Phase 6)
    model_used      TEXT,
    embedding_at    INTEGER,
    UNIQUE(source, source_path, created_at)
);
CREATE INDEX IF NOT EXISTS idx_reference_jobs_created ON reference_jobs(created_at);

-- ============================================================================
-- companies — Austrian companies of interest (resolved from opendata.host)
-- ============================================================================
CREATE TABLE IF NOT EXISTS companies (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name     TEXT NOT NULL UNIQUE,
    legal_form         TEXT,                   -- 'GmbH', 'AG', etc.
    uid                TEXT,                   -- Austrian UID (VAT)
    firmenbuchnummer   TEXT,                   -- FN number
    primary_domain     TEXT,                   -- best-guess apex
    opendata_status    TEXT,                   -- 'active' | 'geloescht' | 'unknown'
    opendata_checked   INTEGER,
    created_at         INTEGER NOT NULL,
    UNIQUE(uid),
    UNIQUE(firmenbuchnummer)
);
CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(canonical_name);

-- ============================================================================
-- targets — discovered career URLs / ATS slugs / search queries
-- ============================================================================
CREATE TABLE IF NOT EXISTS targets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    reference_id    INTEGER REFERENCES reference_jobs(id) ON DELETE CASCADE,
    ats             TEXT NOT NULL,         -- 'greenhouse' | 'lever' | 'workday' |
                                            -- 'personio' | 'smartrecruiters' |
                                            -- 'successfactors' | 'workable' |
                                            -- 'recruitee' | 'karriere_at' |
                                            -- 'stepstone_at' | 'jobs_at' |
                                            -- 'willhaben' | 'generic_html' |
                                            -- 'sitemap_xml' | 'rss'
    source_kind     TEXT NOT NULL,         -- 'ats_board' | 'ats_search' | 'sitemap' |
                                            -- 'aggregator_query' | 'career_path'
    url             TEXT NOT NULL,
    token           TEXT,                  -- ATS board token / company slug
    discovered_at   INTEGER NOT NULL,
    last_attempted  INTEGER,
    last_status     INTEGER,
    last_error      TEXT,
    priority        INTEGER DEFAULT 100,   -- lower = try first
    enabled         INTEGER DEFAULT 1,
    notes           TEXT,
    UNIQUE(ats, url)
);
CREATE INDEX IF NOT EXISTS idx_targets_ats        ON targets(ats);
CREATE INDEX IF NOT EXISTS idx_targets_company    ON targets(company_id);
CREATE INDEX IF NOT EXISTS idx_targets_reference  ON targets(reference_id);
CREATE INDEX IF NOT EXISTS idx_targets_priority   ON targets(priority, enabled);
CREATE INDEX IF NOT EXISTS idx_targets_last       ON targets(last_attempted);

-- ============================================================================
-- fetch_log — dedupe index. Every network fetch consults this BEFORE going out.
-- ============================================================================
CREATE TABLE IF NOT EXISTS fetch_log (
    url_hash        TEXT PRIMARY KEY,         -- sha256(url) — lower hex
    url             TEXT NOT NULL UNIQUE,
    first_checked   INTEGER NOT NULL,
    last_checked    INTEGER NOT NULL,
    last_status     INTEGER,
    last_etag       TEXT,
    last_modified   TEXT,
    last_changed_at INTEGER,
    fetch_count     INTEGER DEFAULT 1,
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_fetch_log_last ON fetch_log(last_checked);
CREATE INDEX IF NOT EXISTS idx_fetch_log_status ON fetch_log(last_status);

-- ============================================================================
-- austria_jobs — the canonical job postings index.
-- Dedupe is on `url` alone (not (job_id, url) like jrf uses) so the same
-- posting mirrored across two ATS feeds collapses to one row.
-- ============================================================================
CREATE TABLE IF NOT EXISTS austria_jobs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    url               TEXT NOT NULL UNIQUE,
    url_hash          TEXT NOT NULL,
    source_domain     TEXT NOT NULL,
    ats               TEXT,
    job_id_at_source  TEXT,
    title             TEXT NOT NULL,
    company           TEXT NOT NULL,
    company_id        INTEGER REFERENCES companies(id),
    location          TEXT,
    postal_code       TEXT,
    country           TEXT DEFAULT 'AT',
    remote_policy     TEXT,                   -- 'on_site' | 'hybrid' | 'remote' | 'unknown'
    employment_type   TEXT,                   -- 'full_time' | 'part_time' | 'contract' | 'intern' | 'unknown'
    seniority         TEXT,                   -- 'junior' | 'mid' | 'senior' | 'lead' | 'unknown'
    salary_min        INTEGER,
    salary_max        INTEGER,
    salary_currency   TEXT,
    salary_period     TEXT,                   -- 'year' | 'month' | 'hour'
    language          TEXT,                   -- 'de' | 'en' | 'mixed'
    description       TEXT,                   -- full description text (cleaned)
    description_html  TEXT,                   -- original HTML
    skills_json       TEXT,                   -- JSON array of detected skills
    first_seen_at     INTEGER NOT NULL,
    last_checked_at   INTEGER NOT NULL,
    last_changed_at   INTEGER,
    status            TEXT DEFAULT 'active',  -- 'active' | 'expired' | 'closed'
    UNIQUE(url)
);
CREATE INDEX IF NOT EXISTS idx_austria_jobs_company  ON austria_jobs(company);
CREATE INDEX IF NOT EXISTS idx_austria_jobs_ats      ON austria_jobs(ats);
CREATE INDEX IF NOT EXISTS idx_austria_jobs_status   ON austria_jobs(status);
CREATE INDEX IF NOT EXISTS idx_austria_jobs_seen     ON austria_jobs(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_austria_jobs_urlhash  ON austria_jobs(url_hash);

-- ============================================================================
-- job_chunks — split descriptions for RAG / similarity
-- ============================================================================
CREATE TABLE IF NOT EXISTS job_chunks (
    chunk_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES austria_jobs(id) ON DELETE CASCADE,
    chunk_type      TEXT NOT NULL,            -- 'description' | 'requirements' | 'responsibilities' | 'meta'
    content         TEXT NOT NULL,
    idx             INTEGER DEFAULT 0,
    created_at      INTEGER NOT NULL,
    CHECK (chunk_type IN ('description','requirements','responsibilities','meta'))
);
CREATE INDEX IF NOT EXISTS idx_chunks_job ON job_chunks(job_id);

-- FTS5 virtual table for keyword search
CREATE VIRTUAL TABLE IF NOT EXISTS job_chunks_fts USING fts5(
    chunk_id UNINDEXED,
    job_id   UNINDEXED,
    chunk_type,
    content,
    tokenize = 'porter unicode61'
);

-- ============================================================================
-- job_chunks_embeddings — vector store
-- ============================================================================
CREATE TABLE IF NOT EXISTS job_chunks_embeddings (
    chunk_id        INTEGER PRIMARY KEY REFERENCES job_chunks(chunk_id) ON DELETE CASCADE,
    embedding_json  TEXT NOT NULL,
    model_used      TEXT NOT NULL,
    generated_at    INTEGER NOT NULL
);

-- ============================================================================
-- skill_aliases — canonical skill normalization
-- ============================================================================
CREATE TABLE IF NOT EXISTS skill_aliases (
    alias           TEXT NOT NULL,
    canonical       TEXT NOT NULL,
    PRIMARY KEY (alias, canonical)
);
CREATE INDEX IF NOT EXISTS idx_skill_aliases_canonical ON skill_aliases(canonical);

-- Pre-seed common skill aliases (idempotent)
INSERT OR IGNORE INTO skill_aliases (alias, canonical) VALUES
    ('rust',          'Rust'),
    ('rs',            'Rust'),
    ('python',        'Python'),
    ('py',            'Python'),
    ('typescript',    'TypeScript'),
    ('ts',            'TypeScript'),
    ('javascript',    'JavaScript'),
    ('js',            'JavaScript'),
    ('golang',        'Go'),
    (' go ',          'Go'),
    ('java',          'Java'),
    ('kotlin',        'Kotlin'),
    ('swift',         'Swift'),
    ('c++',           'C++'),
    ('cpp',           'C++'),
    ('c#',            'C#'),
    ('csharp',        'C#'),
    ('react',         'React'),
    ('reactjs',       'React'),
    ('vue',           'Vue'),
    ('vuejs',         'Vue'),
    ('angular',       'Angular'),
    ('svelte',        'Svelte'),
    ('django',        'Django'),
    ('flask',         'Flask'),
    ('fastapi',       'FastAPI'),
    ('spring',        'Spring'),
    ('springboot',    'Spring Boot'),
    ('spring boot',   'Spring Boot'),
    ('node',          'Node.js'),
    ('nodejs',        'Node.js'),
    ('node.js',       'Node.js'),
    ('postgres',      'PostgreSQL'),
    ('postgresql',    'PostgreSQL'),
    ('mysql',         'MySQL'),
    ('mongodb',       'MongoDB'),
    ('redis',         'Redis'),
    ('kafka',         'Kafka'),
    ('rabbitmq',      'RabbitMQ'),
    ('aws',           'AWS'),
    ('azure',         'Azure'),
    ('gcp',           'GCP'),
    ('kubernetes',    'Kubernetes'),
    ('k8s',           'Kubernetes'),
    ('docker',        'Docker'),
    ('terraform',     'Terraform'),
    ('ansible',       'Ansible'),
    ('helm',          'Helm'),
    ('linux',         'Linux'),
    ('sql',           'SQL'),
    ('graphql',       'GraphQL'),
    ('grpc',          'gRPC'),
    ('rest',          'REST'),
    ('microservices', 'Microservices'),
    ('ml',            'Machine Learning'),
    ('machine learning', 'Machine Learning'),
    ('deep learning', 'Deep Learning'),
    ('nlp',           'NLP'),
    ('llm',           'LLM'),
    ('devops',        'DevOps'),
    ('sre',           'SRE'),
    ('security',      'Security'),
    ('embedded',      'Embedded'),
    ('iot',           'IoT'),
    ('webassembly',   'WebAssembly'),
    ('wasm',          'WebAssembly');

-- ============================================================================
-- detection_events — anti-bot detection log (per anti-bot-three-pillars skill)
-- ============================================================================
CREATE TABLE IF NOT EXISTS detection_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    domain          TEXT NOT NULL,
    url             TEXT,
    pillar          TEXT NOT NULL,    -- 'tls' | 'http2' | 'browser' | 'behavioral' | 'ip_asn' | 'nav_noise' | 'referrer'
    signal          TEXT NOT NULL,    -- free-form description
    response_code   INTEGER,
    occurred_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_detection_domain ON detection_events(domain, occurred_at);

-- ============================================================================
-- pipeline_runs — observability for the orchestrator
-- ============================================================================
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_id    INTEGER REFERENCES reference_jobs(id),
    started_at      INTEGER NOT NULL,
    finished_at     INTEGER,
    status          TEXT,             -- 'running' | 'ok' | 'partial' | 'error'
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON pipeline_runs(started_at);

-- ============================================================================
-- wishlist — discrimination-before-fetch artefact (Pillar 0b, PITFALLS.md).
-- When MAX_FETCH_PER_RUN or the daily budget blocks a target, we save it
-- here so the next run can pick up where this one stopped.
-- ============================================================================
CREATE TABLE IF NOT EXISTS wishlist (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_id          INTEGER NOT NULL REFERENCES reference_jobs(id) ON DELETE CASCADE,
    url                   TEXT NOT NULL,
    url_hash              TEXT NOT NULL,
    source_kind           TEXT NOT NULL,    -- 'ats_board' | 'aggregator_query' | 'sitemap' | 'career_path'
    ats                   TEXT,
    company_name          TEXT,
    predicted_relevance   REAL,             -- 0.0-1.0; below MIN_PREDICTED_RELEVANCE we don't wishlist either
    wishlisted_at         INTEGER NOT NULL,
    fetched_at            INTEGER,          -- NULL until actually fetched
    wishlist_status       TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'fetched' | 'skipped' | 'wontfix'
    skip_reason           TEXT,             -- human-readable if status='skipped'
    UNIQUE(reference_id, url)
);
CREATE INDEX IF NOT EXISTS idx_wishlist_ref_status ON wishlist(reference_id, wishlist_status);
CREATE INDEX IF NOT EXISTS idx_wishlist_pending ON wishlist(wishlist_status, predicted_relevance DESC);
CREATE INDEX IF NOT EXISTS idx_wishlist_urlhash ON wishlist(url_hash);

-- ============================================================================
-- circuit_breaker — per-domain cool-off tracking (Pillar 0 rule 5)
-- When a domain fails N consecutive times, we stop trying for COOLDOWN seconds.
-- ============================================================================
CREATE TABLE IF NOT EXISTS circuit_breaker (
    domain                TEXT PRIMARY KEY,
    consecutive_failures  INTEGER NOT NULL DEFAULT 0,
    last_failure_at       INTEGER,
    last_status_code      INTEGER,
    last_error            TEXT,
    opened_at             INTEGER,        -- when the breaker tripped (NULL = closed)
    cooldown_until        INTEGER,        -- unix timestamp; NULL = no cool-off
    total_attempts        INTEGER NOT NULL DEFAULT 0,
    total_failures        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cb_cooldown ON circuit_breaker(cooldown_until);

-- ============================================================================
-- daily_request_counter — fast budget enforcement (Pillar 0 rule 2)
-- Updated atomically by the fetcher; reset at the day boundary.
-- Kept as a row per (day_utc, is_cf_site) so a single SELECT returns the count.
-- ============================================================================
CREATE TABLE IF NOT EXISTS daily_request_counter (
    day_utc               TEXT NOT NULL,           -- YYYY-MM-DD
    is_cf_site            INTEGER NOT NULL,        -- 0 = normal, 1 = WAF-protected
    request_count         INTEGER NOT NULL DEFAULT 0,
    last_request_at       INTEGER,
    PRIMARY KEY (day_utc, is_cf_site)
);
