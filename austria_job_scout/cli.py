"""CLI — argparse with one subcommand per pipeline module.

Each subcommand is independently runnable. The `pipeline` subcommand chains
all of them.

Usage examples (iter 2):

    # initialise / migrate the SQLite DB (idempotent)
    python -m austria_job_scout db-init
    python -m austria_job_scout db-init --db /tmp/x.db

    # inspect the DB
    python -m austria_job_scout db-stats

    # ingest a reference job from the command line
    python -m austria_job_scout ingest --role "Senior Rust Engineer"
    python -m austria_job_scout ingest --input job.txt
    python -m austria_job_scout ingest --input job.pdf --save

    # discover targets for a reference (no network by default)
    python -m austria_job_scout discover --reference ref.json
    python -m austria_job_scout discover --reference ref.json --probe-seed-paths
    python -m austria_job_scout discover --reference ref.json --out targets.json

    # fetch the targets (NETWORK; honours Pillar 0 + 0b)
    python -m austria_job_scout fetch --targets targets.json
    python -m austria_job_scout fetch --targets targets.json --out responses.json
    python -m austria_job_scout fetch --targets targets.json --no-navigation-noise

    # iter-3/4 subcommands — explicit NotImplementedError until then
    python -m austria_job_scout extract --raw raw.json
    python -m austria_job_scout index --jobs jobs.json
    python -m austria_job_scout score --reference ref.json
    python -m austria_job_scout report --scored scored.json --format md --out report.md
    python -m austria_job_scout pipeline --role "Senior Rust Engineer"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import config, db
from .modules import ingest as ingest_mod, target_discovery, fetcher as fetcher_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json_arg(arg: str):
    """Load JSON from a file path, '-' (stdin), or inline JSON string.

    Raises a ValueError with a user-friendly message on parse or file errors.
    """
    import json as _json
    if arg == "-":
        return _json.loads(sys.stdin.read())
    stripped = arg.strip()
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            return _json.loads(arg)
        except _json.JSONDecodeError as e:
            raise ValueError(f"Invalid inline JSON: {e}")
    path = Path(arg)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {arg}")
    try:
        return _json.loads(path.read_text(encoding="utf-8"))
    except _json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {arg}: {e}")


def _job_to_dict(job) -> dict:
    """Convert an ATSJob or AggregatorJob to a JSON-serializable dict."""
    result = {}
    for field in ("url", "title", "company", "location", "description",
                  "skills", "seniority", "employment_type", "remote",
                  "salary_min", "salary_max", "currency", "posted_date"):
        val = getattr(job, field, None)
        if val is not None:
            result[field] = val
    # AggregatorJob has a single 'salary' string field instead of min/max
    salary = getattr(job, "salary", None)
    if salary is not None:
        result["salary"] = salary
    return result


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_db_init(args: argparse.Namespace) -> int:
    db.init_db(Path(args.db) if args.db else None)
    v = db.schema_version(Path(args.db) if args.db else None)
    print(json.dumps({"status": "ok", "schema_version": v, "db": str(args.db or config.db_path())}, indent=2))
    return 0


def cmd_db_stats(args: argparse.Namespace) -> int:
    print(json.dumps(db.stats(Path(args.db) if args.db else None), indent=2))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    if (args.input is None) == (args.role is None):
        print("error: pass exactly one of --input or --role", file=sys.stderr)
        return 2
    try:
        ref = ingest_mod.ingest_input(
            input_path=Path(args.input) if args.input else None,
            role=args.role,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.save:
        rid = ingest_mod.ingest_to_db(ref, Path(args.db) if args.db else None)
        ref_dict = ref.to_dict()
        ref_dict["id"] = rid
        print(json.dumps(ref_dict, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(ref.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    """Build a ranked Target list from a ReferenceJob.

    --reference can be either:
      - a path to a JSON file (output of `ingest`)
      - a JSON string on stdin (piped)
    """
    if args.reference == "-":
        ref_dict = json.loads(sys.stdin.read())
    elif args.reference.startswith(("{", "[")):
        # Inline JSON — don't try Path() (OSError on long strings).
        ref_dict = json.loads(args.reference)
    elif Path(args.reference).is_file():
        ref_dict = json.loads(Path(args.reference).read_text())
    else:
        try:
            ref_dict = json.loads(args.reference)
        except json.JSONDecodeError:
            print(f"error: --reference is neither a file nor inline JSON: {args.reference!r}",
                  file=sys.stderr)
            return 1

    # Reconstruct a minimal ReferenceJob for the orchestrator. We only need
    # the fields it reads; for full-fidelity, run ingest --save first.
    from dataclasses import fields
    from .modules.ingest import ReferenceJob
    valid_keys = {f.name for f in fields(ReferenceJob)}
    ref_kwargs = {k: v for k, v in ref_dict.items() if k in valid_keys}
    ref = ReferenceJob(**ref_kwargs)

    targets = target_discovery.discover(
        ref,
        include_seeds=not args.no_seeds,
        include_aggregators=not args.no_aggregators,
        probe_seed_paths=args.probe_seed_paths,
        min_relevance=args.min_relevance,
    )

    payload = {
        "reference_id": ref_dict.get("id"),
        "target_count": len(targets),
        "config": {
            "aggressive_mode": config.AGGRESSIVE_MODE,
            "max_targets_per_run": config.MAX_TARGETS_PER_RUN,
            "min_relevance": args.min_relevance,
        },
        "targets": targets,
    }

    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"wrote {len(targets)} targets to {args.out}", file=sys.stderr)
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """Fetch a list of targets. NETWORK. Honours Pillar 0 + Pillar 0b."""
    if args.targets == "-":
        target_list = json.loads(sys.stdin.read())
        if isinstance(target_list, dict):
            target_list = target_list.get("targets", [])
    elif args.targets.startswith(("{", "[")):
        # Inline JSON — don't try Path() (OSError on long strings).
        target_list = json.loads(args.targets)
        if isinstance(target_list, dict):
            target_list = target_list.get("targets", [])
    elif Path(args.targets).is_file():
        data = json.loads(Path(args.targets).read_text())
        target_list = data.get("targets", data) if isinstance(data, dict) else data
    else:
        try:
            target_list = json.loads(args.targets)
        except json.JSONDecodeError:
            print(f"error: --targets is neither a file nor inline JSON: {args.targets!r}",
                  file=sys.stderr)
            return 1

    responses = fetcher_mod.fetch(
        target_list,
        db_path=Path(args.db) if args.db else None,
        navigation_noise=not args.no_navigation_noise,
        max_fetches=args.max_fetches,
    )

    payload = {
        "fetched_count": len(responses),
        "cached_count": sum(1 for r in responses if r.cached),
        "blocked_count": len(fetcher_mod.fetch.last_blocked),
        "blocked": fetcher_mod.fetch.last_blocked,
        "config": {
            "aggressive_mode": config.AGGRESSIVE_MODE,
            "max_fetches_per_run": args.max_fetches or config.MAX_FETCH_PER_RUN,
            "daily_budget_residential": config.DAILY_BUDGET_RESIDENTIAL,
        },
        "responses": [
            {
                "url": r.url,
                "status_code": r.status_code,
                "elapsed_ms": r.elapsed_ms,
                "cached": r.cached,
                "error": r.error,
                "ats_fingerprint": r.ats_fingerprint,
                "fetched_at": r.fetched_at,
                "blocked_reason": r.blocked_reason,
            }
            for r in responses
        ],
    }

    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"wrote {len(responses)} responses to {args.out}", file=sys.stderr)
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    """Extract structured job data from pre-fetched responses."""
    from .modules.extractor import extract
    from .modules.fetcher import RawResponse

    raw_data = _load_json_arg(args.raw)
    if not isinstance(raw_data, list):
        print("error: --raw must be a JSON array of response objects", file=sys.stderr)
        return 1

    results = []
    for item in raw_data:
        resp = RawResponse(
            url=item.get("url", ""),
            status_code=item.get("status_code"),
            text=item.get("text"),
            ats_fingerprint=item.get("ats_fingerprint", "unknown"),
        )
        jobs = extract(resp)
        for j in jobs:
            results.append(_job_to_dict(j))

    print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    """Index extracted jobs into embeddings."""
    from .modules.indexer import JobIndexer

    jobs_data = _load_json_arg(args.jobs)
    if not isinstance(jobs_data, list):
        print("error: --jobs must be a JSON array", file=sys.stderr)
        return 1

    indexer = JobIndexer(use_ml=args.ml)
    indexed = []
    for j in jobs_data:
        ij = indexer.index_job(
            url=j.get("url", ""),
            title=j.get("title"),
            company=j.get("company"),
            location=j.get("location"),
            description=j.get("description"),
            skills=j.get("skills", []),
            seniority=j.get("seniority"),
            employment_type=j.get("employment_type"),
            remote=j.get("remote"),
            salary_min=j.get("salary_min"),
            salary_max=j.get("salary_max"),
        )
        indexed.append({
            "url": ij.url,
            "job_id": ij.job_id,
            "title": ij.title,
            "embedding_dim": len(ij.embedding) if ij.embedding is not None else 0,
        })

    print(json.dumps({"indexed": len(indexed), "jobs": indexed}, indent=2, ensure_ascii=False))
    return 0


def _cmd_score(args: argparse.Namespace) -> int:
    """Score candidate jobs against a reference job."""
    from .modules.indexer import JobIndexer
    from .modules.similarity import analyze_similarity

    ref_data = _load_json_arg(args.reference)
    jobs_data = _load_json_arg(args.jobs) if args.jobs else []

    indexer = JobIndexer(use_ml=args.ml)
    ref_indexed = indexer.index_job(
        url=ref_data.get("url", "reference"),
        title=ref_data.get("title"),
        company=ref_data.get("company"),
        description=ref_data.get("description"),
        skills=ref_data.get("skills", []),
    )
    candidates = []
    for j in jobs_data:
        ij = indexer.index_job(
            url=j.get("url", ""),
            title=j.get("title"),
            company=j.get("company"),
            description=j.get("description"),
            skills=j.get("skills", []),
        )
        candidates.append(ij)

    matches = analyze_similarity(
        reference_job=ref_indexed,
        candidate_jobs=candidates,
        top_k=args.top_k,
        min_score=args.min_score,
    )
    result = [
        {
            "title": m.job.title,
            "company": m.job.company,
            "url": m.job.url,
            "score": round(m.overall_score, 3),
            "breakdown": {
                "title": round(m.title_similarity, 3),
                "description": round(m.description_similarity, 3),
                "skills": round(m.skills_overlap, 3),
            },
        }
        for m in matches
    ]
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    """Generate a report from scored matches."""
    from .modules.reporter import generate_text_report, generate_json_report, generate_csv_report

    scored_data = _load_json_arg(args.scored)
    if not isinstance(scored_data, list):
        print("error: --scored must be a JSON array", file=sys.stderr)
        return 1

    from .modules.similarity import JobMatch
    from .modules.indexer import IndexedJob

    matches = []
    for s in scored_data:
        ij = IndexedJob(
            job_id="0",
            url=s.get("url", ""),
            title=s.get("title", ""),
            company=s.get("company", ""),
            location=s.get("location"),
            description=s.get("description"),
        )
        breakdown = s.get("breakdown", {})
        jm = JobMatch(
            job=ij,
            overall_score=s.get("score", 0.0),
            title_similarity=breakdown.get("title", 0.0),
            description_similarity=breakdown.get("description", 0.0),
            skills_overlap=breakdown.get("skills", 0.0),
            location_match=s.get("location_match", False),
            seniority_match=s.get("seniority_match", False),
            salary_compatibility=s.get("salary_compatibility", 0.0),
        )
        matches.append(jm)

    title = args.title or "Job Matches"
    fmt = args.format or "text"

    if fmt == "json":
        report = generate_json_report(title, "N/A", matches)
    elif fmt == "csv":
        report = generate_csv_report(title, matches)
    else:
        report = generate_text_report(title, matches)

    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Report saved to {args.out}", file=sys.stderr)
    else:
        print(report)
    return 0


def _cmd_pipeline(args: argparse.Namespace) -> int:
    """Run the full pipeline end-to-end."""
    from .modules.pipeline import JobScoutPipeline

    ref = str(args.input) if args.input else args.role
    if not ref:
        print("error: pass exactly one of --input or --role", file=sys.stderr)
        return 1

    pipeline = JobScoutPipeline(use_ml=args.ml)
    try:
        results = pipeline.run(
            reference_job=ref,
            output_dir=args.out,
            max_fetches=args.max_fetches,
            min_similarity=args.min_score,
            top_k=args.top_k,
            report_format=args.format or "all",
        )
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(json.dumps({
        "status": results.get("status"),
        "matches": len(results.get("matches", [])),
        "stats": results.get("stats"),
        "reports": results.get("reports"),
    }, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="austria-job-scout",
        description="Find similar Austrian job postings given a reference job.",
    )
    # Global --db lives ONLY on the subparser-parent (not on the top-level parser),
    # so there's no namespace conflict. Top-level --help inherits it via parents=.
    sub_parent = argparse.ArgumentParser(add_help=False)
    sub_parent.add_argument(
        "--db",
        help="path to SQLite DB (overrides env AUSTRIA_JOB_SCOUT_DB)",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("db-init", parents=[sub_parent], help="initialise / migrate the DB schema (idempotent)")
    sp.set_defaults(func=cmd_db_init)

    sp = sub.add_parser("db-stats", parents=[sub_parent], help="print row counts + DB size")
    sp.set_defaults(func=cmd_db_stats)

    sp = sub.add_parser("ingest", parents=[sub_parent], help="parse a reference job from --input file or --role")
    sp.add_argument("--input", help="path to .pdf / .docx / .txt / .md file")
    sp.add_argument("--role", help="free-text role name (alternative to --input)")
    sp.add_argument("--save", action="store_true", help="persist the result to the DB")
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("discover", parents=[sub_parent], help="[iter-2] build ranked Target list from a ReferenceJob")
    sp.add_argument("--reference", required=True,
                    help="path to a JSON file (output of `ingest --save`), or '-' for stdin, or inline JSON")
    sp.add_argument("--out", help="write targets JSON to this file instead of stdout")
    sp.add_argument("--probe-seed-paths", action="store_true",
                    help="HEAD-probe Tier-3 seed companies (uses network; default off)")
    sp.add_argument("--no-seeds", action="store_true", help="skip the curated Austrian-employer seed list")
    sp.add_argument("--no-aggregators", action="store_true", help="skip karriere.at / jobs.at / AMS aggregator queries")
    sp.add_argument("--min-relevance", type=float, default=0.15,
                    help="drop targets below this predicted relevance (0.0-1.0)")
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("fetch", parents=[sub_parent], help="[iter-2] fetch a list of targets (NETWORK — honours Pillar 0 + 0b)")
    sp.add_argument("--targets", required=True,
                    help="path to targets JSON, or '-' for stdin, or inline JSON")
    sp.add_argument("--out", help="write responses JSON to this file instead of stdout")
    sp.add_argument("--max-fetches", type=int, default=None,
                    help="override config.MAX_FETCH_PER_RUN for this invocation")
    sp.add_argument("--no-navigation-noise", action="store_true",
                    help="skip the GET /, GET /jobs referer-chain warm-up (only for tests)")
    sp.set_defaults(func=cmd_fetch)

    # --- extract ---
    sp = sub.add_parser("extract", parents=[sub_parent], help="extract structured job data from pre-fetched responses")
    sp.add_argument("--raw", required=True, help="JSON array of response objects (file path, '-', or inline)")
    sp.set_defaults(func=_cmd_extract)

    # --- index ---
    sp = sub.add_parser("index", parents=[sub_parent], help="index extracted jobs into embeddings")
    sp.add_argument("--jobs", required=True, help="JSON array of extracted job objects")
    sp.add_argument("--ml", action="store_true", help="use sentence-transformers (requires optional dependency)")
    sp.set_defaults(func=_cmd_index)

    # --- score ---
    sp = sub.add_parser("score", parents=[sub_parent], help="score candidate jobs against a reference")
    sp.add_argument("--reference", required=True, help="reference job JSON (file path, '-', or inline)")
    sp.add_argument("--jobs", help="candidate jobs JSON array (default: read from indexed DB)")
    sp.add_argument("--ml", action="store_true", help="use sentence-transformers")
    sp.add_argument("--min-score", type=float, default=0.3, help="minimum similarity score (default: 0.3)")
    sp.add_argument("--top-k", type=int, default=10, help="max matches to return")
    sp.set_defaults(func=_cmd_score)

    # --- report ---
    sp = sub.add_parser("report", parents=[sub_parent], help="generate a report from scored matches")
    sp.add_argument("--scored", required=True, help="JSON array of scored matches")
    sp.add_argument("--format", choices=["text", "json", "csv"], default="text", help="report format")
    sp.add_argument("--title", help="report title (default: 'Job Matches')")
    sp.add_argument("--out", help="write report to file instead of stdout")
    sp.set_defaults(func=_cmd_report)

    # --- pipeline ---
    sp = sub.add_parser("pipeline", parents=[sub_parent], help="run the full pipeline end-to-end")
    sp.add_argument("--input", help="path to reference job file (.pdf/.docx/.txt/.md)")
    sp.add_argument("--role", help="free-text role name (alternative to --input)")
    sp.add_argument("--out", help="output directory for reports")
    sp.add_argument("--max-fetches", type=int, default=None, help="override MAX_FETCH_PER_RUN")
    sp.add_argument("--min-score", type=float, default=0.3, help="minimum similarity score")
    sp.add_argument("--top-k", type=int, default=10, help="number of top matches")
    sp.add_argument("--format", choices=["text", "json", "csv", "all"], default="all", help="report format")
    sp.add_argument("--ml", action="store_true", help="use sentence-transformers")
    sp.set_defaults(func=_cmd_pipeline)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    # Validate similarity weights before doing anything else — fail fast.
    try:
        config.assert_weights_sum_to_one()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4

    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
