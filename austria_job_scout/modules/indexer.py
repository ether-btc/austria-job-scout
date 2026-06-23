"""Indexer: Vector embeddings and similarity search."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class IndexedJob:
    """A job posting with its embedding and metadata."""

    job_id: str
    url: str
    title: str | None
    company: str | None
    location: str | None
    description: str | None
    skills: list[str] = field(default_factory=list)
    seniority: str | None = None
    employment_type: str | None = None
    remote: bool = False
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str = "EUR"
    posted_date: str | None = None
    embedding: np.ndarray = field(default_factory=lambda: np.array([]))
    raw_json: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# TF-IDF based similarity (no external ML deps)
# ---------------------------------------------------------------------------


class SimpleEmbedding:
    """TF-IDF based embedding without external ML dependencies."""

    def __init__(self, max_features: int = 1000):
        self.max_features = max_features
        self.vocabulary: dict[str, int] = {}
        self.idf: dict[str, float] = {}
        self._document_count = 0

    def fit(self, documents: list[str]) -> None:
        """Build vocabulary and IDF weights from documents."""
        self._document_count = len(documents)

        # Build term frequencies
        df: dict[str, int] = {}  # Document frequency
        for doc in documents:
            terms = self._tokenize(doc)
            unique_terms = set(terms)
            for term in unique_terms:
                df[term] = df.get(term, 0) + 1

        # Compute IDF
        self.idf = {}
        for term, freq in df.items():
            if freq >= 2:  # Only keep terms appearing in at least 2 docs
                if len(self.vocabulary) < self.max_features:
                    self.vocabulary[term] = len(self.vocabulary)
                    self.idf[term] = np.log(self._document_count / (freq + 1)) + 1

    def transform(self, text: str) -> np.ndarray:
        """Transform text to TF-IDF vector."""
        if not self.vocabulary:
            return np.array([])

        # Compute term frequencies
        terms = self._tokenize(text)
        tf: dict[str, int] = {}
        for term in terms:
            tf[term] = tf.get(term, 0) + 1

        # Build TF-IDF vector
        vec = np.zeros(len(self.vocabulary))
        for term, idx in self.vocabulary.items():
            vec[idx] = tf.get(term, 0) * self.idf.get(term, 0)

        # Normalize
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        return vec

    def fit_transform(self, documents: list[str]) -> list[np.ndarray]:
        """Fit and transform documents."""
        self.fit(documents)
        return [self.transform(doc) for doc in documents]

    def _tokenize(self, text: str) -> list[str]:
        """Simple tokenization."""
        # Convert to lowercase and extract words
        words = re.findall(r"\b[a-z]{3,}\b", text.lower())
        return words


# ---------------------------------------------------------------------------
# Job indexer
# ---------------------------------------------------------------------------


class JobIndexer:
    """Index jobs and compute similarity."""

    def __init__(self, use_ml: bool = False):
        """Initialize indexer.

        Args:
            use_ml: If True, use sentence-transformers (requires embeddings optional dependency).
                    If False, use TF-IDF fallback.
        """
        self.use_ml = use_ml
        self.embedding_model: Any = None
        self.jobs: list[IndexedJob] = []
        self.tfidf: SimpleEmbedding | None = None

        if self.use_ml:
            try:
                from sentence_transformers import SentenceTransformer

                # Try optimized model first (e5-small-v2 has best quality: 0.84)
                # Fallback to multilingual MiniLM if e5-small-v2 fails
                try:
                    self.embedding_model = SentenceTransformer("intfloat/e5-small-v2")
                except Exception as e:
                    print(f"Failed to load e5-small-v2: {e}. Trying multilingual MiniLM...")
                    try:
                        self.embedding_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
                    except Exception as e2:
                        print(f"Failed to load multilingual MiniLM: {e2}. Falling back to TF-IDF")
                        self.use_ml = False
                        self.tfidf = SimpleEmbedding()
            except ImportError:
                print("sentence-transformers not available, falling back to TF-IDF")
                self.use_ml = False
                self.tfidf = SimpleEmbedding()

    def index_job(
        self,
        url: str,
        title: str | None = None,
        company: str | None = None,
        location: str | None = None,
        description: str | None = None,
        skills: list[str] | None = None,
        seniority: str | None = None,
        employment_type: str | None = None,
        remote: bool = False,
        salary_min: int | None = None,
        salary_max: int | None = None,
        currency: str = "EUR",
        posted_date: str | None = None,
        raw_json: dict[str, Any] | None = None,
    ) -> IndexedJob:
        """Index a single job posting.

        Returns:
            IndexedJob with embedding
        """
        # Generate job ID
        job_id = self._generate_job_id(url)

        # Combine text for embedding
        text_parts = []
        if title:
            text_parts.append(title)
        if company:
            text_parts.append(company)
        if description:
            text_parts.append(description)
        if skills:
            text_parts.extend(skills)

        combined_text = " ".join(text_parts)

        # Compute embedding
        if self.use_ml and self.embedding_model:
            embedding = self.embedding_model.encode(combined_text, convert_to_numpy=True)
        else:
            if self.tfidf is None:
                self.tfidf = SimpleEmbedding()
                # Fit on this document (single document mode)
                self.tfidf.fit([combined_text])
            embedding = self.tfidf.transform(combined_text)

        job = IndexedJob(
            job_id=job_id,
            url=url,
            title=title,
            company=company,
            location=location,
            description=description,
            skills=skills or [],
            seniority=seniority,
            employment_type=employment_type,
            remote=remote,
            salary_min=salary_min,
            salary_max=salary_max,
            currency=currency,
            posted_date=posted_date,
            embedding=embedding,
            raw_json=raw_json or {},
        )

        self.jobs.append(job)
        return job

    def find_similar(self, job_id: str, top_k: int = 5) -> list[tuple[IndexedJob, float]]:
        """Find similar jobs by ID.

        Returns:
            List of (job, similarity_score) tuples
        """
        target_job = next((j for j in self.jobs if j.job_id == job_id), None)
        if not target_job:
            return []

        return self.find_similar_to_embedding(target_job.embedding, top_k, exclude_job_id=job_id)

    def find_similar_to_embedding(
        self, embedding: np.ndarray, top_k: int = 5, exclude_job_id: str | None = None
    ) -> list[tuple[IndexedJob, float]]:
        """Find similar jobs by embedding vector.

        Returns:
            List of (job, similarity_score) tuples
        """
        if len(embedding) == 0 or not self.jobs:
            return []

        similarities = []
        for job in self.jobs:
            if exclude_job_id and job.job_id == exclude_job_id:
                continue

            if len(job.embedding) != len(embedding):
                continue

            # Cosine similarity
            sim = np.dot(job.embedding, embedding) / (np.linalg.norm(job.embedding) * np.linalg.norm(embedding))
            similarities.append((job, sim))

        # Sort by similarity descending
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:top_k]

    def save(self, path: Path) -> None:
        """Save index to disk (simple JSON format)."""
        import json

        data = []
        for job in self.jobs:
            job_dict = {
                "job_id": job.job_id,
                "url": job.url,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "description": job.description,
                "skills": job.skills,
                "seniority": job.seniority,
                "employment_type": job.employment_type,
                "remote": job.remote,
                "salary_min": job.salary_min,
                "salary_max": job.salary_max,
                "currency": job.currency,
                "posted_date": job.posted_date,
                "embedding": job.embedding.tolist(),
                "raw_json": job.raw_json,
            }
            data.append(job_dict)

        path.write_text(json.dumps(data, indent=2))

    def load(self, path: Path) -> None:
        """Load index from disk."""
        import json

        data = json.loads(path.read_text())
        self.jobs = []

        for job_dict in data:
            job = IndexedJob(
                job_id=job_dict["job_id"],
                url=job_dict["url"],
                title=job_dict.get("title"),
                company=job_dict.get("company"),
                location=job_dict.get("location"),
                description=job_dict.get("description"),
                skills=job_dict.get("skills", []),
                seniority=job_dict.get("seniority"),
                employment_type=job_dict.get("employment_type"),
                remote=job_dict.get("remote", False),
                salary_min=job_dict.get("salary_min"),
                salary_max=job_dict.get("salary_max"),
                currency=job_dict.get("currency", "EUR"),
                posted_date=job_dict.get("posted_date"),
                embedding=np.array(job_dict.get("embedding", [])),
                raw_json=job_dict.get("raw_json", {}),
            )
            self.jobs.append(job)

    def _generate_job_id(self, url: str) -> str:
        """Generate unique job ID from URL."""
        return hashlib.sha256(url.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fetch_job_details(url: str) -> dict[str, Any]:
    """Fetch job details from a URL (simple HTML parsing)."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        return {}

    soup = BeautifulSoup(response.text, "lxml")

    # Try JSON-LD
    scripts = soup.find_all("script", type="application/ld+json")
    for script in scripts:
        try:
            import json

            data = json.loads(script.string or "{}")
            if isinstance(data, dict) and data.get("@type") == "JobPosting":
                return data
        except (json.JSONDecodeError, AttributeError):
            continue

    # Fallback: basic extraction
    title = soup.find("h1") or soup.find("h2")
    return {
        "title": title.get_text(strip=True) if title else None,
        "url": url,
    }