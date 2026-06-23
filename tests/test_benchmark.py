"""Tests for the benchmark module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from austria_job_scout.benchmark import (
    time_delay_mechanism,
    time_embedding_model,
)


def test_embedding_benchmark_runs():
    """Embedding benchmark completes without errors."""
    result = time_embedding_model(iterations=3)
    
    assert result["iterations"] == 3
    assert result["mean_ms"] >= 0
    assert result["median_ms"] >= 0
    assert len(result["samples"]) == 3
    assert all("iteration" in s and "latency_ms" in s for s in result["samples"])


def test_embedding_benchmark_json_output(tmp_path):
    """Embedding benchmark can write JSON output."""
    from austria_job_scout import cli
    import sys
    
    output_file = tmp_path / "benchmark.json"
    
    # Run benchmark with JSON output
    sys.argv = ["benchmark", "--embedding-only", "--iterations", "3", "--json", "--out", str(output_file)]
    
    from austria_job_scout.benchmark import main
    result = main()
    
    assert output_file.exists()
    data = json.loads(output_file.read_text())
    assert "benchmarks" in data
    assert "embedding" in data["benchmarks"]


def test_delay_benchmark_runs():
    """Delay benchmark completes and shows long-tail distribution."""
    result = time_delay_mechanism(iterations=10)
    
    assert result["iterations"] == 10
    assert result["mean_ms"] > 0
    assert result["median_ms"] > 0
    assert result["stdev_ms"] > 0  # Should have variance due to long-tail
    assert result["long_pause_count"] >= 0
    assert result["long_pause_pct"] >= 0
    assert len(result["samples"]) == 10


def test_delay_benchmark_long_tail_distribution():
    """Verify the delay mechanism produces a long-tail distribution."""
    result = time_delay_mechanism(iterations=20)
    
    # With 20 iterations at 15% long-pause rate, we expect 0-6 long pauses
    # (binomial distribution, but this is a sanity check)
    assert result["long_pause_count"] <= 10  # Shouldn't be ALL long pauses
    assert result["stdev_ms"] > result["mean_ms"] * 0.5  # High variance expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])