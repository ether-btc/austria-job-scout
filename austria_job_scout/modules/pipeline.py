"""Pipeline orchestrator: End-to-end workflow."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from austria_job_scout.extractors.ats_extractor import (
    ATSJob,
    extract_from_html as extract_ats_html,
)
from austria_job_scout.extractors.aggregator_extractor import (
    AggregatorJob,
    extract_from_html as extract_aggregator_html,
)
from .. import config
from austria_job_scout.modules.fetcher import (
    RawResponse,
    fetch as fetch_targets,
)
from austria_job_scout.modules.indexer import JobIndexer, IndexedJob
from austria_job_scout.modules.ingest import ingest_input as ingest
from austria_job_scout.modules.reporter import (
    ReportConfig,
    generate_csv_report,
    generate_json_report,
    generate_text_report,
)
from austria_job_scout.modules.similarity import JobMatch, analyze_similarity
from austria_job_scout.modules.target_discovery import discover as discover_targets

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class JobScoutPipeline:
    """End-to-end pipeline for finding similar Austrian jobs."""

    def __init__(
        self,
        db_path: str | None = None,
        use_ml: bool = False,
    ):
        """Initialize pipeline.

        Args:
            db_path: Path to SQLite database. If None, uses default from settings.
            use_ml: If True, use sentence-transformers for embeddings.
        """
        self.db_path = db_path or config.DEFAULT_DB_PATH
        self.use_ml = use_ml

        # Initialize components
        self.indexer = JobIndexer(use_ml=use_ml)
        self.conn = None

        logger.info(f"Pipeline initialized (use_ml={use_ml})")

    def run(
        self,
        reference_job: Path | str,
        output_dir: Path | str | None = None,
        max_fetches: int | None = None,
        min_similarity: float = 0.5,
        top_k: int = 10,
        report_format: str = "text",
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the full pipeline.

        Args:
            reference_job: Path to PDF/docx/text file or role name string
            output_dir: Directory to save reports. If None, uses current dir.
            max_fetches: Maximum number of URLs to fetch. If None, uses budget.
            min_similarity: Minimum similarity score (0-1)
            top_k: Number of top matches to report
            report_format: Report format (text, json, csv, all)
            filters: Optional filters (location, seniority, remote, salary_min)

        Returns:
            Dictionary with pipeline results and stats
        """
        logger.info("Starting pipeline run...")

        # Step 1: Ingest reference job
        logger.info("Step 1: Ingesting reference job...")
        # If it's a Path-like that doesn't exist on disk OR a non-path string,
        # treat it as a role name (free-text). The CLI and programmatic callers
        # pass roles; tests with PDF/TXT paths use the file path.
        if isinstance(reference_job, (str, Path)) and str(reference_job).strip():
            s = str(reference_job)
            p = Path(s)
            # Heuristic: if it looks like a path and exists on disk → file
            if p.exists() and p.is_file():
                ref_job = ingest(input_path=p)
            else:
                ref_job = ingest(role=s)
        else:
            ref_job = ingest(reference_job)
        if not ref_job:
            raise ValueError(f"Failed to ingest reference job from: {reference_job}")

        logger.info(f"Reference job: {ref_job.title} (skills: {len(ref_job.skills)})")

        # Step 2: Discover targets
        logger.info("Step 2: Discovering target URLs...")
        targets = discover_targets(ref_job)

        # Apply budget limit
        max_allowed = max_fetches or config.MAX_FETCH_PER_RUN
        targets_to_fetch = targets[:max_allowed]

        logger.info("Found %d targets, fetching %d", len(targets), len(targets_to_fetch))

        # Step 3: Fetch targets (with stealth)
        logger.info("Step 3: Fetching targets (with stealth protection)...")
        try:
            fetched = fetch_targets(targets_to_fetch)
        except Exception as e:
            # fetch() re-raises DailyBudgetExhausted. We cannot recover the
            # partial results from outside the function, so we continue with
            # whatever was fetched before the exception (empty list).
            # NOTE: fetch.last_blocked is list[dict], NOT list[RawResponse].
            logger.warning("Fetch stopped early (%s); continuing with empty results", e)
            fetched = []

        if not fetched:
            logger.warning("No targets were fetched successfully")
            return {
                "status": "completed",
                "reference": {"title": ref_job.title, "url": ref_job.url or "N/A"},
                "matches": [],
                "stats": {
                    "targets_discovered": len(targets),
                    "targets_fetched": 0,
                    "jobs_extracted": 0,
                },
            }

        logger.info(f"Fetched {len(fetched)} targets")

        # Step 4: Extract job details (from pre-fetched HTML — no double-fetch)
        logger.info("Step 4: Extracting job details...")
        extracted_jobs = []

        for resp in fetched:
            if resp.text is None or resp.status_code is None or resp.status_code >= 400:
                continue
            ats = resp.ats_fingerprint or "unknown"
            if any(a in ats for a in ["workday", "greenhouse", "lever", "smartrecruiters", "personio"]):
                ats_job = extract_ats_html(resp.url, resp.text)
                if ats_job:
                    extracted_jobs.append(ats_job)
            else:
                agg_jobs = extract_aggregator_html(resp.url, resp.text)
                if agg_jobs:
                    extracted_jobs.extend(agg_jobs)

        # Dedupe by content_hash (title + company) — same job on two sources
        from austria_job_scout.extractors.ats_extractor import dedupe_jobs
        before_dedup = len(extracted_jobs)
        extracted_jobs = dedupe_jobs(extracted_jobs)
        if before_dedup != len(extracted_jobs):
            logger.info(
                "Dedup: %d jobs → %d unique (dropped %d duplicates)",
                before_dedup, len(extracted_jobs), before_dedup - len(extracted_jobs),
            )

        logger.info(f"Extracted {len(extracted_jobs)} jobs")

        # Step 5: Index jobs
        logger.info("Step 5: Indexing jobs...")
        indexed_jobs = []

        # Index reference job first
        ref_indexed = self.indexer.index_job(
            url=ref_job.url or "reference",
            title=ref_job.title,
            company=ref_job.company,
            location=ref_job.location,
            description=ref_job.description,
            skills=ref_job.skills,
            seniority=ref_job.seniority,
            employment_type=ref_job.employment_type,
            remote=ref_job.remote,
        )
        indexed_jobs.append(ref_indexed)

        # Index extracted jobs
        for job in extracted_jobs:
            indexed_job = self.indexer.index_job(
                url=job.url,
                title=job.title,
                company=job.company,
                location=job.location,
                description=job.description,
                skills=job.skills,
                seniority=job.seniority,
                employment_type=job.employment_type,
                remote=job.remote,
                salary_min=job.salary_min,
                salary_max=job.salary_max,
                currency=job.currency,
                posted_date=job.posted_date,
                raw_json=job.raw_json,
            )
            indexed_jobs.append(indexed_job)

        logger.info(f"Indexed {len(indexed_jobs)} jobs")

        # Step 6: Find similar jobs
        logger.info("Step 6: Finding similar jobs...")
        matches = analyze_similarity(
            reference_job=ref_indexed,
            candidate_jobs=indexed_jobs[1:],  # Skip reference
            top_k=top_k,
            min_score=min_similarity,
            filters=filters,
        )

        logger.info(f"Found {len(matches)} matches (score >= {min_similarity})")

        # Step 7: Generate reports
        logger.info("Step 7: Generating reports...")
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        output_dir.mkdir(parents=True, exist_ok=True)

        reports = {}

        if report_format in ("text", "all"):
            text_report = generate_text_report(ref_job.title or "Untitled", matches)
            text_path = output_dir / "similar_jobs_report.md"
            text_path.write_text(text_report)
            reports["text"] = str(text_path)
            logger.info(f"Text report saved to: {text_path}")

        if report_format in ("json", "all"):
            json_report = generate_json_report(
                ref_job.title or "Untitled",
                ref_job.url or "N/A",
                matches,
            )
            json_path = output_dir / "similar_jobs_report.json"
            import json

            json_path.write_text(json.dumps(json_report, indent=2))
            reports["json"] = str(json_path)
            logger.info(f"JSON report saved to: {json_path}")

        if report_format in ("csv", "all"):
            csv_report = generate_csv_report(ref_job.title or "Untitled", matches)
            csv_path = output_dir / "similar_jobs_report.csv"
            csv_path.write_text(csv_report)
            reports["csv"] = str(csv_path)
            logger.info(f"CSV report saved to: {csv_path}")

        # Compile results
        results = {
            "status": "completed",
            "reference": {
                "title": ref_job.title,
                "url": ref_job.url or "N/A",
                "skills": ref_job.skills,
            },
            "matches": [
                {
                    "title": m.job.title,
                    "company": m.job.company,
                    "url": m.job.url,
                    "score": m.overall_score,
                }
                for m in matches
            ],
            "reports": reports,
            "stats": {
                "targets_discovered": len(targets),
                "targets_fetched": len(fetched),
                "jobs_extracted": len(extracted_jobs),
                "jobs_indexed": len(indexed_jobs),
                "matches_found": len(matches),
            },
        }

        logger.info("Pipeline completed successfully")
        return results


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


