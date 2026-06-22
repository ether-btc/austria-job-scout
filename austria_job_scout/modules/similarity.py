"""Similarity analysis: compare job postings and find matches."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from austria_job_scout.modules.indexer import IndexedJob

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SimilarityResult:
    """Result of a similarity comparison."""

    target_job: IndexedJob
    similar_jobs: list[tuple[IndexedJob, float]]
    metadata: dict[str, Any] | None = None


@dataclass
class JobMatch:
    """A matched job with scoring breakdown."""

    job: IndexedJob
    overall_score: float  # 0-1
    title_similarity: float  # 0-1
    description_similarity: float  # 0-1
    skills_overlap: float  # 0-1
    location_match: bool
    seniority_match: bool
    salary_compatibility: float  # 0-1


# ---------------------------------------------------------------------------
# Similarity analyzer
# ---------------------------------------------------------------------------


class SimilarityAnalyzer:
    """Analyze similarity between job postings."""

    def __init__(self):
        """Initialize analyzer."""
        self.jobs: list[IndexedJob] = []

    def add_job(self, job: IndexedJob) -> None:
        """Add a job to the index."""
        self.jobs.append(job)

    def find_matches(
        self,
        reference_job: IndexedJob,
        top_k: int = 10,
        min_score: float = 0.5,
        filters: dict[str, Any] | None = None,
    ) -> list[JobMatch]:
        """Find matching jobs for a reference.

        Args:
            reference_job: The job to find matches for
            top_k: Maximum number of results
            min_score: Minimum similarity score (0-1)
            filters: Optional filters (location, seniority, etc.)

        Returns:
            List of JobMatch objects sorted by overall_score descending
        """
        matches = []

        for job in self.jobs:
            if job.job_id == reference_job.job_id:
                continue

            # Apply filters
            if filters and not self._passes_filters(job, filters):
                continue

            # Compute detailed similarity
            match = self._compute_match(reference_job, job)

            if match.overall_score >= min_score:
                matches.append(match)

        # Sort by overall score
        matches.sort(key=lambda m: m.overall_score, reverse=True)
        return matches[:top_k]

    def _compute_match(self, ref: IndexedJob, candidate: IndexedJob) -> JobMatch:
        """Compute detailed match between two jobs."""
        # Embedding similarity (primary signal)
        if len(ref.embedding) > 0 and len(candidate.embedding) > 0:
            embedding_sim = np.dot(ref.embedding, candidate.embedding) / (
                np.linalg.norm(ref.embedding) * np.linalg.norm(candidate.embedding)
            )
        else:
            embedding_sim = 0.0

        # Title similarity
        title_sim = self._text_similarity(ref.title or "", candidate.title or "")

        # Description similarity
        desc_sim = self._text_similarity(ref.description or "", candidate.description or "")

        # Skills overlap
        skills_overlap = self._skills_overlap(ref.skills, candidate.skills)

        # Location match
        location_match = self._location_match(ref.location, candidate.location)

        # Seniority match
        seniority_match = self._seniority_match(ref.seniority, candidate.seniority)

        # Salary compatibility
        salary_compat = self._salary_compatibility(ref, candidate)

        # Weighted overall score
        # Adjust weights based on your priorities
        weights = {
            "embedding": 0.3,
            "title": 0.2,
            "description": 0.2,
            "skills": 0.15,
            "location": 0.05,
            "seniority": 0.05,
            "salary": 0.05,
        }

        overall_score = (
            weights["embedding"] * embedding_sim
            + weights["title"] * title_sim
            + weights["description"] * desc_sim
            + weights["skills"] * skills_overlap
            + weights["location"] * (1.0 if location_match else 0.0)
            + weights["seniority"] * (1.0 if seniority_match else 0.0)
            + weights["salary"] * salary_compat
        )

        return JobMatch(
            job=candidate,
            overall_score=overall_score,
            title_similarity=title_sim,
            description_similarity=desc_sim,
            skills_overlap=skills_overlap,
            location_match=location_match,
            seniority_match=seniority_match,
            salary_compatibility=salary_compat,
        )

    def _text_similarity(self, text1: str, text2: str) -> float:
        """Compute simple text similarity using word overlap."""
        if not text1 or not text2:
            return 0.0

        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())

        if not words1 or not words2:
            return 0.0

        intersection = len(words1.intersection(words2))
        union = len(words1.union(words2))

        return intersection / union if union > 0 else 0.0

    def _skills_overlap(self, skills1: list[str], skills2: list[str]) -> float:
        """Compute skills overlap ratio."""
        if not skills1 or not skills2:
            return 0.0

        set1 = {s.lower() for s in skills1}
        set2 = {s.lower() for s in skills2}

        intersection = len(set1.intersection(set2))
        union = len(set1.union(set2))

        return intersection / union if union > 0 else 0.0

    def _location_match(self, loc1: str | None, loc2: str | None) -> bool:
        """Check if locations match (case-insensitive)."""
        if not loc1 or not loc2:
            return False

        return loc1.lower().strip() == loc2.lower().strip()

    def _seniority_match(self, seniority1: str | None, seniority2: str | None) -> bool:
        """Check if seniority levels match."""
        if not seniority1 or not seniority2:
            return False

        # Normalize seniority terms
        s1_norm = self._normalize_seniority(seniority1)
        s2_norm = self._normalize_seniority(seniority2)

        return s1_norm == s2_norm

    def _normalize_seniority(self, seniority: str) -> str:
        """Normalize seniority level."""
        s = seniority.lower()
        if any(kw in s for kw in ["senior", "lead", "principal"]):
            return "senior"
        elif any(kw in s for kw in ["mid", "intermediate"]):
            return "mid"
        elif any(kw in s for kw in ["junior", "entry", "intern"]):
            return "junior"
        elif any(kw in s for kw in ["executive", "director", "head", "vp"]):
            return "executive"
        else:
            return "other"

    def _salary_compatibility(self, ref: IndexedJob, candidate: IndexedJob) -> float:
        """Check salary range compatibility."""
        if not ref.salary_min and not ref.salary_max:
            return 1.0  # No salary constraint

        if not candidate.salary_min and not candidate.salary_max:
            return 0.5  # Unknown salary

        # Check if ranges overlap
        ref_min = ref.salary_min or 0
        ref_max = ref.salary_max or ref_min * 2  # Assume 2x max if unspecified
        cand_min = candidate.salary_min or 0
        cand_max = candidate.salary_max or cand_min * 2

        # Overlap calculation
        overlap_min = max(ref_min, cand_min)
        overlap_max = min(ref_max, cand_max)

        if overlap_max < overlap_min:
            return 0.0  # No overlap

        # Calculate overlap ratio
        overlap_range = overlap_max - overlap_min
        total_range = max(ref_max - ref_min, cand_max - cand_min)

        return overlap_range / total_range if total_range > 0 else 0.0

    def _passes_filters(self, job: IndexedJob, filters: dict[str, Any]) -> bool:
        """Check if job passes filters."""
        # Location filter
        if "location" in filters and job.location:
            if filters["location"].lower() not in job.location.lower():
                return False

        # Seniority filter
        if "seniority" in filters and job.seniority:
            ref_seniority = self._normalize_seniority(filters["seniority"])
            job_seniority = self._normalize_seniority(job.seniority)
            if ref_seniority != job_seniority:
                return False

        # Remote filter
        if "remote" in filters:
            if filters["remote"] and not job.remote:
                return False
            if not filters["remote"] and job.remote:
                return False

        # Salary filter (minimum)
        if "salary_min" in filters and job.salary_min:
            if job.salary_min < filters["salary_min"]:
                return False

        return True


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def analyze_similarity(
    reference_job: IndexedJob,
    candidate_jobs: list[IndexedJob],
    top_k: int = 10,
    min_score: float = 0.5,
    filters: dict[str, Any] | None = None,
) -> list[JobMatch]:
    """Convenience function to analyze similarity without creating an analyzer.

    Args:
        reference_job: The job to find matches for
        candidate_jobs: List of jobs to compare against
        top_k: Maximum number of results
        min_score: Minimum similarity score
        filters: Optional filters

    Returns:
        List of JobMatch objects
    """
    analyzer = SimilarityAnalyzer()
    for job in candidate_jobs:
        analyzer.add_job(job)

    return analyzer.find_matches(reference_job, top_k, min_score, filters)