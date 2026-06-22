"""Shared pytest fixtures.

Every test gets a fresh temp DB so they're truly isolated. The path is
exposed via the `tmp_db` fixture.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Add project root to sys.path at conftest import time for collection/imports
_project_root = Path(__file__).parent.parent.resolve()
_str_project_root = str(_project_root)
if _str_project_root not in sys.path:
    sys.path.insert(0, _str_project_root)


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch) -> Path:
    """Fresh SQLite DB per test, env-pointed at it."""
    p = tmp_path / "austria_jobs.db"
    monkeypatch.setenv("AUSTRIA_JOB_SCOUT_DB", str(p))
    return p


@pytest.fixture()
def no_sleep(monkeypatch):
    """Disable the human-like random delays in the fetcher.

    Tests using this fixture must also mock ``_http_get`` to avoid real
    network calls. Together, this lets us exercise the full Pillar 0 / 0b
    guard stack without burning 5-25s of test wall time per fetch.
    """
    monkeypatch.setattr(
        "austria_job_scout.modules.fetcher._sleep_like_human",
        lambda: None,
    )


@pytest.fixture()
def zero_budget(monkeypatch):
    """Allow unlimited daily budget in tests (override the residential cap)."""
    monkeypatch.setattr(
        "austria_job_scout.config.DAILY_BUDGET_RESIDENTIAL", 10_000,
    )
    monkeypatch.setattr(
        "austria_job_scout.config.MAX_FETCH_PER_RUN", 10_000,
    )


@pytest.fixture()
def sample_role() -> str:
    return "Senior Rust Backend Engineer"


@pytest.fixture()
def sample_english_jd() -> str:
    return """
Senior Backend Engineer (Rust)
Company: Acme Cloud GmbH
Location: Wien

We are looking for a Senior Rust engineer to join our team.
You will work on distributed systems and microservices using Rust, PostgreSQL,
and Kubernetes. Experience with AWS, Docker, and gRPC is a plus.

Requirements:
- 5+ years of Rust
- Strong knowledge of async Rust, Tokio
- Experience with PostgreSQL, Redis, Kafka
- Comfort with Linux, Docker, Kubernetes
- Good communication skills
"""
