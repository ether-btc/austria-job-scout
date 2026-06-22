"""Pipeline orchestrator: End-to-end workflow."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from austria_job_scout.extractors.ats_extractor import (
    ATSJob,
    extract_from_url as extract_ats_url,
)
from austria_job_scout.extractors.aggregator_extractor import (
    AggregatorJob,
    extract_from_url as extract_aggregator_url,
)
from austria_job_scout.modules.config import settings
from austria_job_scout.modules.db import init_db
from austria_job_scout.modules.fetcher import (
    Target,
    fetch_targets,
    respect_daily_budget,
)
from austria_job_scout.modules.indexer import JobIndexer, IndexedJob
from austria_job_scout.modules.ingest import ingest
from austria_job_scout.modules.reporter import (
    ReportConfig,
    generate_csv_report,
    generate_json_report,
    generate_text_report,
)
from austria_job_scout.modules.similarity import JobMatch, analyze_similarity
from austria_job_scout.modules.target_discovery import discover_targets

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
        self.db_path = db_path or settings.DB_PATH
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
        ref_job = ingest(reference_job)
        if not ref_job:
            raise ValueError(f"Failed to ingest reference job from: {reference_job}")

        logger.info(f"Reference job: {ref_job.title} (skills: {len(ref_job.skills)})")

        # Step 2: Discover targets
        logger.info("Step 2: Discovering target URLs...")
        targets = discover_targets(ref_job)

        # Apply budget limit
        max_allowed = max_fetches or self._get_max_fetches()
        targets_to_fetch = targets[:max_allowed]

        logger.info(f"Found {len(targets)} targets, fetching {len(targets_to_fetch)}")

        # Step 3: Fetch targets (with stealth)
        logger.info("Step 3: Fetching targets (with stealth protection)...")
        fetched = fetch_targets(targets_to_fetch)

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

        # Step 4: Extract job details
        logger.info("Step 4: Extracting job details...")
        extracted_jobs = []

        for target in fetched:
            # Detect ATS vs aggregator
            if any(ats in target.ats for ats in ["workday", "greenhouse", "lever", "smartrecruiters"]):
                # ATS extraction
                ats_job = extract_ats_url(target.url)
                if ats_job:
                    extracted_jobs.append(ats_job)
            else:
                # Aggregator extraction
                agg_jobs = extract_aggregator_url(target.url)
                if agg_jobs:
                    extracted_jobs.extend(agg_jobs)

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

    def _get_max_fetches(self) -> int:
        """Get maximum fetches based on budget."""
        if respect_daily_budget():
            return settings.MAX_FETCH_PER_RUN
        else:
            logger.warning("Daily budget exhausted, returning 0 fetches")
            return 0


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def run_pipeline(
    reference_job: Path | str,
    output_dir: Path | str | None = None,
    use_ml: bool = False,
    max_fetches: int | None = None,
    min_similarity: float = 0.5,
    top_k: int = 10,
    report_format: str = "text",
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full pipeline (convenience function).

    Args:
        reference_job: Path to PDF/docx/text file or role name string
        output_dir: Directory to save reports
        use_ml: If True, use sentence-transformers for embeddings
        max_fetches: Maximum number of URLs to fetch
        min_similarity: Minimum similarity score (0-1)
        top_k: Number of top matches to report
        report_format: Report format (text, json, csv, all)
        filters: Optional filters

    Returns:
        Dictionary with pipeline results
    """
    pipeline = JobScoutPipeline(use_ml=use_ml)
    return pipeline.run(
        reference_job=reference_job,
        output_dir=output_dir,
        max_fetches=max_fetches,
        min_similarity=min_similarity,
        top_k=top_k,
        report_format=report_format,
        filters=filters,
    )