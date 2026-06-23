#!/usr/bin/env python3
"""Performance benchmark for austria-job-scout.

Measures:
- Embedding model latency (TF-IDF or sentence-transformers)
- Delay mechanism timing (long-tail distribution)
- End-to-end module timing

Usage:
    python -m austria_job_scout.benchmark --help
    python -m austria_job_scout.benchmark --embedding-only
    python -m austria_job_scout.benchmark --delay-only
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from austria_job_scout import cli
from austria_job_scout.config import AGGRESSIVE_MODE
from austria_job_scout.modules.indexer import JobIndexer


def time_embedding_model(iterations: int = 10) -> dict[str, Any]:
    """Benchmark the embedding model latency."""
    indexer = JobIndexer(use_ml=False)  # Use TF-IDF by default
    texts = [
        "Senior Rust Backend Developer with 5+ years experience",
        "Python Engineer for machine learning infrastructure",
        "DevOps Engineer specializing in Kubernetes and AWS",
        "Frontend Developer with React and TypeScript expertise",
        "Data Scientist for predictive analytics and ML ops",
    ] * (iterations // 5 + 1)
    texts = texts[:iterations]

    latencies_ms = []
    results = []

    model_type = "sentence-transformers" if indexer.use_ml else "TF-IDF"
    model_name = getattr(indexer.embedding_model, 'get_config_string', lambda: model_type)() if indexer.use_ml else model_type

    print(f"\n=== Embedding Model Benchmark ({iterations} iterations) ===")
    print(f"Model type: {model_type}")
    if indexer.use_ml and indexer.embedding_model:
        print(f"Model: {indexer.embedding_model}")
    print()

    for i, text in enumerate(texts, 1):
        start = time.perf_counter()
        # Index a job to trigger embedding
        job = indexer.index_job(
            url=f"https://example.com/job{i}",
            title=text,
            company="Test Company",
            description=text,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies_ms.append(elapsed_ms)
        results.append({"iteration": i, "latency_ms": round(elapsed_ms, 2)})
        print(f"  [{i}/{iterations}] {elapsed_ms:6.1f} ms")

    mean_lat = statistics.mean(latencies_ms)
    median_lat = statistics.median(latencies_ms)
    stdev_lat = statistics.stdev(latencies_ms) if len(latencies_ms) > 1 else 0
    min_lat = min(latencies_ms)
    max_lat = max(latencies_ms)

    summary = {
        "model_type": model_type,
        "iterations": iterations,
        "mean_ms": round(mean_lat, 2),
        "median_ms": round(median_lat, 2),
        "stdev_ms": round(stdev_lat, 2),
        "min_ms": round(min_lat, 2),
        "max_ms": round(max_lat, 2),
        "samples": results,
    }

    print()
    print(f"  Mean:   {mean_lat:6.1f} ms")
    print(f"  Median: {median_lat:6.1f} ms")
    print(f"  Stdev:  {stdev_lat:6.1f} ms")
    print(f"  Min:    {min_lat:6.1f} ms")
    print(f"  Max:    {max_lat:6.1f} ms")
    print()

    return summary


def time_delay_mechanism(iterations: int = 20) -> dict[str, Any]:
    """Benchmark the human-like delay distribution."""
    from austria_job_scout.modules.fetcher import _sleep_like_human

    print(f"\n=== Delay Mechanism Benchmark ({iterations} iterations) ===")
    print("Testing long-tail distribution (5-25s base, 15% chance of 60-180s)")
    print("(Using scaled-down delays for benchmark: divide by 10)")
    print()

    # Override the sleep function temporarily to use scaled delays
    import austria_job_scout.modules.fetcher as fetcher_module
    original_sleep = fetcher_module.time.sleep

    delays_ms = []
    results = []

    def scaled_sleep(seconds):
        # Scale down by 10x for benchmark
        scaled = seconds / 10.0
        start = time.perf_counter()
        original_sleep(scaled)
        actual = (time.perf_counter() - start) * 1000
        return actual

    fetcher_module.time.sleep = scaled_sleep

    try:
        for i in range(1, iterations + 1):
            start = time.perf_counter()
            _sleep_like_human()
            elapsed_ms = (time.perf_counter() - start) * 1000
            delays_ms.append(elapsed_ms)
            results.append({"iteration": i, "delay_ms": round(elapsed_ms, 2)})
            print(f"  [{i}/{iterations}] {elapsed_ms:6.1f} ms")
    finally:
        fetcher_module.time.sleep = original_sleep

    mean_delay = statistics.mean(delays_ms)
    median_delay = statistics.median(delays_ms)
    stdev_delay = statistics.stdev(delays_ms) if len(delays_ms) > 1 else 0

    # Count long pauses (should be ~15% of iterations)
    long_pause_threshold_ms = 6000  # 600ms = 6s scaled
    long_pauses = sum(1 for d in delays_ms if d > long_pause_threshold_ms)
    long_pause_pct = (long_pauses / iterations) * 100

    summary = {
        "iterations": iterations,
        "mean_ms": round(mean_delay, 2),
        "median_ms": round(median_delay, 2),
        "stdev_ms": round(stdev_delay, 2),
        "long_pause_count": long_pauses,
        "long_pause_pct": round(long_pause_pct, 1),
        "expected_long_pause_pct": 15.0,
        "samples": results,
    }

    print()
    print(f"  Mean:              {mean_delay:6.1f} ms")
    print(f"  Median:            {median_delay:6.1f} ms")
    print(f"  Stdev:             {stdev_delay:6.1f} ms")
    print(f"  Long pauses:       {long_pauses}/{iterations} ({long_pause_pct:.1f}%, expected ~15%)")
    print()

    return summary


def benchmark_cli_command(argv: list[str], tmp_db: Path) -> dict[str, Any]:
    """Time a CLI command execution."""
    final_argv = argv + ["--db", str(tmp_db)]

    start = time.perf_counter()
    rc = cli.main(final_argv)
    elapsed_ms = (time.perf_counter() - start) * 1000

    return {
        "command": " ".join(argv),
        "exit_code": rc,
        "elapsed_ms": round(elapsed_ms, 2),
    }


def run_full_pipeline_benchmark(role: str, tmp_db: Path) -> dict[str, Any]:
    """Run a scaled-down pipeline benchmark."""
    print("\n=== Full Pipeline Benchmark ===")
    print(f"Role: {role}")
    print(f"DB: {tmp_db}")
    print()

    timings = {}

    # 1. db-init
    print("  [1/4] Initializing database...")
    timings["db_init"] = benchmark_cli_command(["db-init"], tmp_db)
    print(f"        {timings['db_init']['elapsed_ms']:.1f} ms")

    # 2. ingest
    print("  [2/4] Ingesting role...")
    ingest_result = benchmark_cli_command(["ingest", "--role", role, "--save"], tmp_db)
    timings["ingest"] = ingest_result
    print(f"        {ingest_result['elapsed_ms']:.1f} ms")

    # Note: discover, fetch, index would require JSON piping between commands
    # For a real benchmark, the user should run the pipeline command
    print("  [3/4] Discover/Fetch/Index... (requires full pipeline run)")
    print("  [4/4] Use `austria-job-scout pipeline --role '...'` for end-to-end")

    return {"timings": timings}


def main():
    parser = argparse.ArgumentParser(description="Performance benchmark for austria-job-scout")
    parser.add_argument("--embedding-only", action="store_true",
                       help="Only benchmark the embedding model")
    parser.add_argument("--delay-only", action="store_true",
                       help="Only benchmark the delay mechanism")
    parser.add_argument("--full-pipeline", action="store_true",
                       help="Run full pipeline benchmark")
    parser.add_argument("--role", type=str, default="Senior Rust Engineer",
                       help="Role name for pipeline benchmark")
    parser.add_argument("--iterations", type=int, default=10,
                       help="Number of iterations for benchmarks")
    parser.add_argument("--json", action="store_true",
                       help="Output results as JSON")
    parser.add_argument("--out", type=str,
                       help="Write JSON results to file")

    args = parser.parse_args()

    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aggressive_mode": AGGRESSIVE_MODE,
        "benchmarks": {},
    }

    try:
        if args.embedding_only or (not args.delay_only and not args.full_pipeline):
            results["benchmarks"]["embedding"] = time_embedding_model(args.iterations)

        if args.delay_only or (not args.embedding_only and not args.full_pipeline):
            results["benchmarks"]["delay"] = time_delay_mechanism(args.iterations)

        if args.full_pipeline:
            tmp_db = Path("/tmp/austria_benchmark.db")
            tmp_db.unlink(missing_ok=True)
            results["benchmarks"]["pipeline"] = run_full_pipeline_benchmark(args.role, tmp_db)
            tmp_db.unlink(missing_ok=True)

    except KeyboardInterrupt:
        print("\nBenchmark interrupted by user")
        sys.exit(130)

    if args.json or args.out:
        output = json.dumps(results, indent=2)
        if args.out:
            Path(args.out).write_text(output)
            print(f"Results written to {args.out}")
        else:
            print(output)

    return results


if __name__ == "__main__":
    main()