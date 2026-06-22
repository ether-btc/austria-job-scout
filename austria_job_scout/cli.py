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


def _cmd_stub(name: str) -> int:
    def _fn(_args: argparse.Namespace) -> int:
        print(f"error: `{name}` is iter-3/4 work. See .planning/01-1-PLAN.md", file=sys.stderr)
        return 3
    _fn.__name__ = f"cmd_{name}"
    return _fn


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

    for name in ("extract", "index", "score", "report"):
        sp = sub.add_parser(name, parents=[sub_parent], help=f"[iter-3/4] {name} subcommand")
        sp.set_defaults(func=_cmd_stub(name))

    sp = sub.add_parser("pipeline", parents=[sub_parent], help="[iter-4] run all subcommands end-to-end")
    sp.set_defaults(func=_cmd_stub("pipeline"))

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
