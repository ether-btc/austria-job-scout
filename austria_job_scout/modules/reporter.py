"""Reporter: Generate reports from similarity results."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from austria_job_scout.modules.similarity import JobMatch

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ReportConfig:
    """Configuration for report generation."""

    include_details: bool = True
    include_scores: bool = True
    include_skill_overlap: bool = True
    include_salary_info: bool = True
    max_results: int = 10
    sort_by: str = "overall_score"  # overall_score, title_similarity, skills_overlap
    language: str = "en"  # en, de


# ---------------------------------------------------------------------------
# Report generators
# ---------------------------------------------------------------------------


def generate_text_report(
    reference_title: str,
    matches: list[JobMatch],
    config: ReportConfig | None = None,
) -> str:
    """Generate a human-readable text report.

    Args:
        reference_title: Title of the reference job
        matches: List of matching jobs
        config: Report configuration

    Returns:
        Markdown-formatted report
    """
    if config is None:
        config = ReportConfig()

    lines = []

    # Header
    lines.append(f"# Similar Jobs Report")
    lines.append(f"")
    lines.append(f"**Reference Job:** {reference_title}")
    lines.append(f"**Matches Found:** {len(matches)}")
    lines.append(f"")

    if not matches:
        lines.append("No similar jobs found matching your criteria.")
        return "\n".join(lines)

    # Sort matches
    if config.sort_by == "title_similarity":
        matches = sorted(matches, key=lambda m: m.title_similarity, reverse=True)
    elif config.sort_by == "skills_overlap":
        matches = sorted(matches, key=lambda m: m.skills_overlap, reverse=True)
    else:
        matches = sorted(matches, key=lambda m: m.overall_score, reverse=True)

    matches = matches[: config.max_results]

    # Summary stats
    avg_score = sum(m.overall_score for m in matches) / len(matches) if matches else 0
    avg_title_sim = sum(m.title_similarity for m in matches) / len(matches) if matches else 0
    avg_skills = sum(m.skills_overlap for m in matches) / len(matches) if matches else 0

    lines.append("## Summary")
    lines.append(f"")
    lines.append(f"- **Average Overall Score:** {avg_score:.2%}")
    lines.append(f"- **Average Title Similarity:** {avg_title_sim:.2%}")
    lines.append(f"- **Average Skills Overlap:** {avg_skills:.2%}")
    lines.append(f"")

    # Detailed results
    lines.append("## Similar Jobs")
    lines.append("")

    for i, match in enumerate(matches, 1):
        job = match.job

        lines.append(f"### {i}. {job.title or 'Untitled'}")
        lines.append("")

        # Basic info
        if job.company:
            lines.append(f"**Company:** {job.company}")
        if job.location:
            lines.append(f"**Location:** {job.location}")
        if job.url:
            lines.append(f"**URL:** {job.url}")
        lines.append("")

        # Scores
        if config.include_scores:
            lines.append("**Similarity Scores:**")
            lines.append(f"- Overall: {match.overall_score:.2%}")
            lines.append(f"- Title: {match.title_similarity:.2%}")
            lines.append(f"- Description: {match.description_similarity:.2%}")
            lines.append(f"- Skills: {match.skills_overlap:.2%}")
            lines.append("")

        # Skills overlap
        if config.include_skill_overlap and job.skills:
            lines.append(f"**Skills:** {', '.join(job.skills)}")
            lines.append("")

        # Salary info
        if config.include_salary_info and (job.salary_min or job.salary_max):
            if job.salary_min and job.salary_max:
                lines.append(f"**Salary:** €{job.salary_min:,} - €{job.salary_max:,}")
            elif job.salary_min:
                lines.append(f"**Salary:** €{job.salary_min:,}+")
            elif job.salary_max:
                lines.append(f"**Salary:** Up to €{job.salary_max:,}")
            lines.append("")

        # Additional details
        if config.include_details:
            if job.seniority:
                lines.append(f"**Seniority:** {job.seniority}")
            if job.employment_type:
                lines.append(f"**Employment Type:** {job.employment_type}")
            if job.remote:
                lines.append(f"**Remote:** Yes")
            if job.posted_date:
                lines.append(f"**Posted:** {job.posted_date}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def generate_json_report(
    reference_title: str,
    reference_url: str,
    matches: list[JobMatch],
    config: ReportConfig | None = None,
) -> dict[str, Any]:
    """Generate a structured JSON report.

    Args:
        reference_title: Title of the reference job
        reference_url: URL of the reference job
        matches: List of matching jobs
        config: Report configuration

    Returns:
        Dictionary with report data
    """
    if config is None:
        config = ReportConfig()

    # Sort matches
    if config.sort_by == "title_similarity":
        matches = sorted(matches, key=lambda m: m.title_similarity, reverse=True)
    elif config.sort_by == "skills_overlap":
        matches = sorted(matches, key=lambda m: m.skills_overlap, reverse=True)
    else:
        matches = sorted(matches, key=lambda m: m.overall_score, reverse=True)

    matches = matches[: config.max_results]

    # Build report
    report = {
        "reference": {
            "title": reference_title,
            "url": reference_url,
        },
        "summary": {
            "total_matches": len(matches),
            "avg_overall_score": sum(m.overall_score for m in matches) / len(matches) if matches else 0,
            "avg_title_similarity": sum(m.title_similarity for m in matches) / len(matches) if matches else 0,
            "avg_skills_overlap": sum(m.skills_overlap for m in matches) / len(matches) if matches else 0,
        },
        "matches": [],
    }

    for match in matches:
        job = match.job

        match_data = {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "url": job.url,
            "scores": {
                "overall": match.overall_score,
                "title": match.title_similarity,
                "description": match.description_similarity,
                "skills": match.skills_overlap,
            },
        }

        if config.include_skill_overlap and job.skills:
            match_data["skills"] = job.skills

        if config.include_salary_info:
            if job.salary_min or job.salary_max:
                match_data["salary"] = {
                    "min": job.salary_min,
                    "max": job.salary_max,
                    "currency": job.currency,
                }

        if config.include_details:
            match_data["details"] = {
                "seniority": job.seniority,
                "employment_type": job.employment_type,
                "remote": job.remote,
                "posted_date": job.posted_date,
            }

        report["matches"].append(match_data)

    return report


def generate_csv_report(
    reference_title: str,
    matches: list[JobMatch],
    config: ReportConfig | None = None,
) -> str:
    """Generate a CSV report.

    Args:
        reference_title: Title of the reference job
        matches: List of matching jobs
        config: Report configuration

    Returns:
        CSV-formatted report
    """
    if config is None:
        config = ReportConfig()

    # Sort matches
    matches = sorted(matches, key=lambda m: m.overall_score, reverse=True)
    matches = matches[: config.max_results]

    # CSV header
    header = ["Rank", "Title", "Company", "Location", "URL", "Overall Score"]
    if config.include_scores:
        header.extend(["Title Similarity", "Description Similarity", "Skills Overlap"])
    if config.include_skill_overlap:
        header.append("Skills")
    if config.include_salary_info:
        header.extend(["Salary Min", "Salary Max", "Currency"])
    if config.include_details:
        header.extend(["Seniority", "Employment Type", "Remote", "Posted Date"])

    lines = [",".join(header)]

    # Data rows
    for i, match in enumerate(matches, 1):
        job = match.job

        row = [
            str(i),
            job.title or "",
            job.company or "",
            job.location or "",
            job.url or "",
            f"{match.overall_score:.4f}",
        ]

        if config.include_scores:
            row.extend([
                f"{match.title_similarity:.4f}",
                f"{match.description_similarity:.4f}",
                f"{match.skills_overlap:.4f}",
            ])

        if config.include_skill_overlap:
            row.append('"{}"'.format('", "'.join(job.skills)) if job.skills else "")

        if config.include_salary_info:
            row.extend([
                str(job.salary_min) if job.salary_min else "",
                str(job.salary_max) if job.salary_max else "",
                job.currency,
            ])

        if config.include_details:
            row.extend([
                job.seniority or "",
                job.employment_type or "",
                "Yes" if job.remote else "",
                job.posted_date or "",
            ])

        lines.append(",".join(row))

    return "\n".join(lines)