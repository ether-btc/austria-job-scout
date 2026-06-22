"""Tests for the ingest module.

Covers:
    - free-text role input
    - text-based PDF (text-only fixture file)
    - .txt / .md input
    - language detection (en, de, mixed)
    - skill extraction
    - title / company / location extraction
    - DB persistence
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from austria_job_scout import db
from austria_job_scout.modules import ingest as ingest_mod
from austria_job_scout.probes import language


# ---------------------------------------------------------------------------
# Language probe
# ---------------------------------------------------------------------------

def test_language_en_clear(sample_english_jd):
    assert language.detect(sample_english_jd) == "en"


def test_language_de_clear():
    text = (
        "Wir suchen einen Senior Backend Entwickler für unser Team in Wien. "
        "Sie werden mit Rust, PostgreSQL und Kubernetes arbeiten. "
        "Bitte bewerben Sie sich mit Ihren vollständigen Unterlagen."
    )
    assert language.detect(text) == "de"


def test_language_mixed():
    text = (
        "Senior Rust engineer needed. Sie werden mit Rust und PostgreSQL arbeiten. "
        "Experience with AWS and Docker is required. Kenntnisse in Linux erforderlich."
    )
    assert language.detect(text) == "mixed"


def test_language_unknown_short():
    assert language.detect("hi") == "unknown"
    assert language.detect("") == "unknown"


def test_language_queries_de_role():
    q = language.language_search_query(
        "Wir suchen einen Senior Rust Backend Entwickler",
        role="Senior Rust Backend Developer",
    )
    assert "de" in q and "en" in q
    # The de query should translate Developer → Entwickler
    assert "Entwickler" in q["de"]


# ---------------------------------------------------------------------------
# Skill extraction
# ---------------------------------------------------------------------------

def test_skills_extract_common():
    text = "Looking for a Rust developer with PostgreSQL, Docker, and AWS experience."
    skills = ingest_mod.extract_skills(text)
    assert "Rust" in skills
    assert "PostgreSQL" in skills
    assert "Docker" in skills
    assert "AWS" in skills


def test_skills_no_false_java_in_javascript():
    text = "React developer needed, JavaScript/TypeScript."
    skills = ingest_mod.extract_skills(text)
    # JavaScript present, JavaScript NOT confused with Java
    assert "JavaScript" in skills
    # Java must NOT appear unless the literal word "Java" is there
    assert "Java" not in skills


def test_skills_unique_preserve_order():
    text = "Rust, Python, Rust, Go, Python"
    skills = ingest_mod.extract_skills(text)
    # Longest-match wins: Python (6 chars) is checked before Rust (4) before Go (2).
    assert skills == ["Python", "Rust", "Go"]


def test_skills_unique_preserve_order_appearance():
    """When skills have the same length, appearance order in text wins."""
    text = "Rust, Go, Python"   # all distinct single-pass; longest is Rust=4
    skills = ingest_mod.extract_skills(text)
    # Order: "Rust" matches first (in length-4 bucket, appears first in text)
    # then "Go" (length 2), then "Python" (length 6) -- actually Python is length 6
    # so Python matches before Rust. Reorder: Python, Rust, Go.
    assert skills == ["Python", "Rust", "Go"]


def test_skills_multiword_preferred_over_subword():
    """Multi-word skill ('Machine Learning') must match before single-word
    learning-only references, when both are present."""
    text = "We need Machine Learning expertise and basic ML knowledge."
    skills = ingest_mod.extract_skills(text)
    assert "Machine Learning" in skills
    # "ML" is in our list — but does it match?
    # The text says "ML knowledge". Regex \bML\b matches "ML" → present.
    assert "ML" in skills
    # Multi-word variant must come first because it's longer.
    assert skills.index("Machine Learning") < skills.index("ML")


def test_skills_empty_input():
    assert ingest_mod.extract_skills("") == []


def test_skills_no_text_returns_empty():
    assert ingest_mod.extract_skills(None) == []


# ---------------------------------------------------------------------------
# Title / company / location extraction
# ---------------------------------------------------------------------------

def test_title_from_jd_explicit_marker():
    text = "Job Title: Senior Rust Engineer\n\nWe are looking for..."
    assert ingest_mod.extract_title_from_text(text) == "Senior Rust Engineer"


def test_title_from_jd_marker_german():
    text = "Position: Senior Backend Entwickler\n\nAufgaben..."
    assert ingest_mod.extract_title_from_text(text) == "Senior Backend Entwickler"


def test_title_from_jd_first_line_fallback():
    text = "Senior Rust Engineer\nWe are looking for someone with Rust experience."
    assert ingest_mod.extract_title_from_text(text) == "Senior Rust Engineer"


def test_company_extraction():
    text = "Company: Acme Cloud GmbH\nLocation: Wien\n..."
    assert ingest_mod.extract_company(text) == "Acme Cloud GmbH"


def test_location_extraction_truncates_at_comma():
    text = "Location: Wien, Österreich\n..."
    assert ingest_mod.extract_location(text) == "Wien"


def test_location_extraction_none_when_missing():
    assert ingest_mod.extract_location("No location here") is None


# ---------------------------------------------------------------------------
# Role prompt cleaning
# ---------------------------------------------------------------------------

def test_role_prompt_strip_leading_verbs():
    assert ingest_mod.extract_title_from_role(
        "We are looking for Senior Rust Engineer"
    ) == "Senior Rust Engineer"
    assert ingest_mod.extract_title_from_role(
        "Wir suchen Senior Rust Engineer"
    ) == "Senior Rust Engineer"
    # "Stelle:" is a label, not a verb — the function leaves it intact,
    # but still strips the trailing (m/w/d) suffix.
    assert ingest_mod.extract_title_from_role(
        "Stelle: Senior Backend Developer (m/w/d)"
    ) == "Stelle: Senior Backend Developer"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def test_ingest_role(sample_role):
    ref = ingest_mod.ingest_input(role=sample_role)
    assert ref.source == "role_name"
    assert ref.title == sample_role
    assert ref.role_query == sample_role
    assert ref.raw_text == sample_role
    assert ref.language in ("en", "de", "mixed", "unknown")
    assert isinstance(ref.skills, list)
    assert isinstance(ref.language_queries, dict)


def test_ingest_txt(sample_english_jd, tmp_path):
    p = tmp_path / "jd.txt"
    p.write_text(sample_english_jd)
    ref = ingest_mod.ingest_input(input_path=p)
    assert ref.source == "txt"
    assert ref.title and "Rust" in ref.title
    assert ref.company == "Acme Cloud GmbH"
    assert ref.location == "Wien"
    assert ref.language == "en"
    assert "Rust" in ref.skills
    assert "PostgreSQL" in ref.skills
    assert "Kubernetes" in ref.skills


def test_ingest_unsupported_extension(tmp_path):
    p = tmp_path / "jd.xyz"
    p.write_text("anything")
    with pytest.raises(ValueError):
        ingest_mod.ingest_input(input_path=p)


def test_ingest_both_args_rejected():
    with pytest.raises(ValueError):
        ingest_mod.ingest_input(input_path=Path("/dev/null"), role="x")


def test_ingest_neither_args_rejected():
    with pytest.raises(ValueError):
        ingest_mod.ingest_input()


def test_ingest_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest_mod.ingest_input(input_path=tmp_path / "no.txt")


def test_ingest_persistence(tmp_db, sample_role):
    db.init_db()
    ref = ingest_mod.ingest_input(role=sample_role)
    rid = ingest_mod.ingest_to_db(ref)
    assert rid > 0

    with db.get_conn_ctx() as conn:
        row = conn.execute(
            "SELECT * FROM reference_jobs WHERE id=?", (rid,)
        ).fetchone()
    assert row is not None
    assert row["source"] == "role_name"
    assert row["language"] in ("en", "de", "mixed", "unknown")
    skills = json.loads(row["skills_json"])
    assert isinstance(skills, list)


def test_ingest_to_db_is_idempotent_per_second(tmp_db, sample_role, monkeypatch):
    """The (source, source_path, created_at) UNIQUE prevents double-insert
    within the same second. Repeated ingest_to_db returns the same id."""
    # Freeze time so both calls land in the same second — otherwise we race
    # across a second boundary and get two distinct (source, source_path, ts)
    # tuples (different ids).
    import time as _time
    monkeypatch.setattr(_time, "time", lambda: 1_700_000_000.0)
    db.init_db()
    ref = ingest_mod.ingest_input(role=sample_role)
    id1 = ingest_mod.ingest_to_db(ref)
    id2 = ingest_mod.ingest_to_db(ref)
    assert id1 == id2
    with db.get_conn_ctx() as conn:
        n = conn.execute("SELECT count(*) FROM reference_jobs").fetchone()[0]
    assert n == 1


# ---------------------------------------------------------------------------
# ReferenceJob dataclass shape (contract test)
# ---------------------------------------------------------------------------

def test_reference_job_to_dict_has_required_keys():
    ref = ingest_mod.ingest_input(role="Senior Rust Engineer")
    d = ref.to_dict()
    for key in (
        "source", "raw_text", "title", "company", "location",
        "language", "skills", "role_query", "language_queries",
        "source_path", "parse_notes",
    ):
        assert key in d, f"missing key {key!r}"
    assert d["source"] == "role_name"
    assert d["raw_text"] == "Senior Rust Engineer"
