"""Tests for the JobIndexer embedding model fallback chain.

Verifies Cycle 3 audit fix: graceful fallback when sentence-transformers
models fail to load. Critical because the previous code would silently
leave `self.embedding_model = None` causing runtime errors.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


def _ensure_sentence_transformers_mock():
    """Ensure sentence_transformers is mockable even if not installed.

    The test environment may not have sentence-transformers installed.
    We pre-register a mock module in sys.modules so the patch() calls work.
    """
    if "sentence_transformers" not in sys.modules:
        # Create a mock module with a SentenceTransformer attribute
        mock_module = MagicMock()
        sys.modules["sentence_transformers"] = mock_module


def test_indexer_falls_back_to_tfidf_when_ml_disabled():
    """When use_ml=False, no ML model is loaded and TF-IDF is used."""
    from austria_job_scout.modules.indexer import JobIndexer

    indexer = JobIndexer(use_ml=False)
    assert indexer.use_ml is False
    assert indexer.embedding_model is None


def test_indexer_loads_e5_small_v2_successfully():
    """When sentence-transformers is available and e5-small-v2 loads, use it."""
    _ensure_sentence_transformers_mock()
    from austria_job_scout.modules.indexer import JobIndexer

    fake_model = object()
    mock_st = MagicMock(return_value=fake_model)

    with patch.dict(sys.modules, {"sentence_transformers": MagicMock(SentenceTransformer=mock_st)}):
        indexer = JobIndexer(use_ml=True)
        assert indexer.use_ml is True
        assert indexer.embedding_model is fake_model
        # Should have been called once with e5-small-v2
        assert mock_st.call_count == 1
        assert mock_st.call_args.args[0] == "intfloat/e5-small-v2"


def test_indexer_falls_back_to_minilm_when_e5_fails(capsys):
    """When e5-small-v2 fails to load, fall back to multilingual MiniLM.

    Regression test for Cycle 3 finding: previously this fallback
    was unhandled - if the fallback model also failed, embedding_model
    would be None causing a runtime error on .encode().
    """
    from austria_job_scout.modules.indexer import JobIndexer

    fake_minilm = object()
    call_count = {"n": 0}

    def fake_constructor(model_name):
        call_count["n"] += 1
        if model_name == "intfloat/e5-small-v2":
            raise RuntimeError("model not found")
        if model_name == "paraphrase-multilingual-MiniLM-L12-v2":
            return fake_minilm
        raise ValueError(f"unexpected model: {model_name}")

    mock_st = MagicMock(side_effect=fake_constructor)
    with patch.dict(sys.modules, {"sentence_transformers": MagicMock(SentenceTransformer=mock_st)}):
        indexer = JobIndexer(use_ml=True)
        assert indexer.use_ml is True
        assert indexer.embedding_model is fake_minilm
        assert call_count["n"] == 2  # tried e5, fell back to MiniLM

        # Verify logging happened
        captured = capsys.readouterr()
        assert "Failed to load e5-small-v2" in captured.out
        assert "Trying multilingual MiniLM" in captured.out


def test_indexer_falls_back_to_tfidf_when_both_ml_models_fail(capsys):
    """When BOTH e5-small-v2 and MiniLM fail, gracefully fall back to TF-IDF.

    This is the critical regression test: previously, if both models
    failed, embedding_model would be None and .encode() would crash
    at runtime. Now we set use_ml=False and initialize SimpleEmbedding.
    """
    from austria_job_scout.modules.indexer import JobIndexer

    def fake_constructor(model_name):
        raise RuntimeError(f"model {model_name} unavailable")

    mock_st = MagicMock(side_effect=fake_constructor)
    with patch.dict(sys.modules, {"sentence_transformers": MagicMock(SentenceTransformer=mock_st)}):
        indexer = JobIndexer(use_ml=True)
        # Critical assertions: safe fallback state
        assert indexer.use_ml is False
        assert indexer.embedding_model is None
        assert indexer.tfidf is not None  # TF-IDF initialized

        # Verify both error messages were logged
        captured = capsys.readouterr()
        assert "Failed to load e5-small-v2" in captured.out
        assert "Failed to load multilingual MiniLM" in captured.out
        assert "Falling back to TF-IDF" in captured.out


def test_indexer_falls_back_to_tfidf_when_sentence_transformers_missing(capsys):
    """When sentence-transformers is not installed, use TF-IDF."""
    import builtins
    from austria_job_scout.modules.indexer import JobIndexer

    # Simulate ImportError on the sentence_transformers import
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("No module named 'sentence_transformers'")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", side_effect=fake_import):
        indexer_obj = JobIndexer(use_ml=True)
        # Critical: graceful fallback when import fails
        assert indexer_obj.use_ml is False
        assert indexer_obj.embedding_model is None
        assert indexer_obj.tfidf is not None

        captured = capsys.readouterr()
        assert "sentence-transformers not available" in captured.out
        assert "falling back to TF-IDF" in captured.out


def test_indexer_tfidf_fallback_can_encode():
    """Verify the TF-IDF fallback path doesn't crash on .index_job().

    Catches the case where the fallback state is set but the encoding
    method fails (e.g., SimpleEmbedding not properly initialized).
    Note: TF-IDF requires .fit() to be called on a corpus first, so
    we just verify the call completes without raising (the previous
    code would crash with AttributeError on None.embedding_model).
    """
    from austria_job_scout.modules.indexer import IndexedJob, JobIndexer

    def fake_constructor(model_name):
        raise RuntimeError("no models available")

    mock_st = MagicMock(side_effect=fake_constructor)
    with patch.dict(sys.modules, {"sentence_transformers": MagicMock(SentenceTransformer=mock_st)}):
        indexer = JobIndexer(use_ml=True)
        # Should not raise AttributeError on None.embedding_model
        result = indexer.index_job(
            url="https://example.com/job/1",
            title="Python Developer",
            company="ACME GmbH",
            description="We are looking for a Python developer with 3+ years experience.",
        )
        # Verify the result is a valid IndexedJob object
        assert isinstance(result, IndexedJob)
        assert result.embedding is not None
        assert result.url == "https://example.com/job/1"
