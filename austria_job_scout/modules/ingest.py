"""Ingest — turn a user input into a ReferenceJob.

Inputs accepted (priority order, set by CLI flags):
    --input <file.pdf>
    --input <file.txt|file.md>
    --input <file.docx>     (python-docx)
    --role "Senior Rust Engineer"   (free-text role name)

Output:
    A :class:`ReferenceJob` (TypedDict-shaped dict) ready for the DB and for
    the next pipeline stage. JSON-serialisable. NO network calls.

Why this matters:
    Every downstream module assumes a normalised ReferenceJob shape. Keeping
    ingest pure (no I/O beyond file read) makes it trivially testable.

References:
    - PITFALLS.md (no fakes, no assumptions about jrf internals)
    - ocr-and-documents skill (for scanned PDFs — marker-pdf path is gated)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from ..probes.language import detect, language_search_query


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------

@dataclass
class ReferenceJob:
    """Normalised input — the canonical shape passed between pipeline stages."""
    source: str                                # 'pdf' | 'txt' | 'role_name' | 'docx'
    raw_text: str
    title: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    language: str = "unknown"                  # 'de' | 'en' | 'mixed' | 'unknown'
    skills: list[str] = field(default_factory=list)
    role_query: Optional[str] = None           # canonical role name for aggregators
    language_queries: dict[str, str] = field(default_factory=dict)
    source_path: Optional[str] = None
    parse_notes: list[str] = field(default_factory=list)   # warnings / "I guessed this"

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Skill extraction (very lightweight v1; replace with jrf.embeddings later)
# ---------------------------------------------------------------------------

# Curated subset — covers the top 80% of engineering keywords. The full table
# is in schema.sql as `skill_aliases` (we can promote to that later).
_KNOWN_SKILLS = sorted({
    "Rust", "Python", "TypeScript", "JavaScript", "Go", "Java", "Kotlin",
    "Swift", "C++", "C#", "React", "Vue", "Angular", "Svelte", "Django",
    "Flask", "FastAPI", "Spring", "Spring Boot", "Node.js", "PostgreSQL",
    "MySQL", "MongoDB", "Redis", "Kafka", "RabbitMQ", "AWS", "Azure", "GCP",
    "Kubernetes", "Docker", "Terraform", "Ansible", "Helm", "Linux", "SQL",
    "GraphQL", "gRPC", "REST", "Microservices", "Machine Learning", "Deep Learning",
    "NLP", "LLM", "DevOps", "SRE", "Security", "Embedded", "IoT", "WebAssembly",
 "Backend", "Frontend", "Fullstack",
 "ML", "AI", "CI", "CD", "UI", "UX", "API", "ETL",
 }, key=len, reverse=True)   # longest first so multi-word skills match before single words

_SENIORITY = ("principal", "staff", "senior", "lead", "junior", "mid", "intern", "praktikum", "berufseinsteiger")


def extract_skills(text: str) -> list[str]:
    """Return the unique list of canonical skills mentioned in *text*.

    Strategy: **longest-match wins**. Multi-word skills like "Machine Learning"
    are checked before single-word subskills like "ML" so the canonical entry
    always wins. Within the same length, iteration order is alphabetical-by-sort
    (effectively deterministic for a fixed ``_KNOWN_SKILLS`` table).

    Uses whole-word matching (``\\b`` boundaries) to avoid ``Java`` matching
    inside ``JavaScript``. Case-insensitive. Duplicates are dropped.

    Returns ``[]`` for empty/None input.
    """
    if not text:
        return []
    found: list[str] = []
    lowered = text.lower()
    seen: set[str] = set()
    for skill in _KNOWN_SKILLS:
        if skill.lower() in seen:
            continue
        pattern = r"(?i)\b" + re.escape(skill) + r"\b"
        if re.search(pattern, lowered):
            found.append(skill)
            seen.add(skill.lower())
    return found


# ---------------------------------------------------------------------------
# Title / role-name extraction
# ---------------------------------------------------------------------------

_TITLE_STOPWORDS = frozenset(
    "suchen sucht hiring looking bewirb dich bewerben role role: position position: "
    "stelle job jobs opportunity opportunities vacature vacatures m/w/d m/w m/f "
    "gn all genders".split()
)


def _strip_role_prompt(text: str) -> str:
    """Remove common prompt prefixes from a free-text role input.

    "Looking for Senior Rust Engineer" → "Senior Rust Engineer"
    "Stelle: Senior Backend Developer (m/w/d)" → "Senior Backend Developer (m/w/d)"
    """
    t = text.strip()
    # Drop common leading verbs (en + de)
    t = re.sub(r"(?i)^(we are looking for|looking for|hire|hiring|suche sucht|suchen|wir suchen)\s+", "", t)
    # Drop "(m/w/d)" type suffixes
    t = re.sub(r"\s*\((m/w/d|m/w|gn|all genders|f/m/d)\)\s*", " ", t, flags=re.IGNORECASE)
    # Drop trailing punctuation
    t = t.strip(" \t\n,.;:")
    return t


def extract_title_from_role(text: str) -> str:
    """Best-effort: take the whole cleaned role string as the title."""
    return _strip_role_prompt(text)


def extract_title_from_text(text: str) -> Optional[str]:
    """Try to pull a job title from a free-form job description.

    Heuristics (in order):
      1. Line starting with a typical "Job Title:" / "Position:" marker.
      2. The first non-empty line if it looks title-ish (≤ 120 chars, contains a
         role keyword).
      3. The first line containing a seniority marker.
    """
    if not text:
        return None
    markers = ("job title", "position", "stelle", "position:", "job title:", "role:")
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if any(low.startswith(m) for m in markers):
            after = re.sub(r"(?i)^(job title|position|stelle|rolle)\s*[:\-]\s*", "", line)
            return after.strip() or None
        # Title-ish? short, has role keywords
        if len(line) <= 120 and any(s in low for s in ("developer", "engineer", "architekt", "manager", "lead", "developerin")):
            return line
        # Has seniority
        if any(s in low.lower() for s in _SENIORITY):
            return line
    return None


# ---------------------------------------------------------------------------
# Company / location (very rough; opendata.host lookup is in iter 2)
# ---------------------------------------------------------------------------

_COMPANY_LIKELY = re.compile(
    r"(?im)^\s*(?:company|unternehmen|firma|arbeitgeber)\s*[:\-]\s*(.+)$"
)
_LOCATION_LIKELY = re.compile(
    r"(?im)^\s*(?:location|standort|ort|place)\s*[:\-]\s*(.+)$"
)


def extract_company(text: str) -> Optional[str]:
    m = _COMPANY_LIKELY.search(text)
    return m.group(1).strip() if m else None


def extract_location(text: str) -> Optional[str]:
    m = _LOCATION_LIKELY.search(text)
    if not m:
        return None
    loc = m.group(1).strip()
    # Take the first segment before any separator (comma, " / ", " - ")
    loc = re.split(r"\s*[,\-/]\s*", loc, maxsplit=1)[0].strip()
    return loc or None


# ---------------------------------------------------------------------------
# File readers
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _read_pdf(path: Path) -> str:
    """Extract text from a text-based PDF using pymupdf.

    For scanned PDFs the user must run the marker-pdf path manually first
    (see ocr-and-documents skill). We don't try OCR here — v1 is text-only
    and we fail loudly so the user knows.
    """
    try:
        import pymupdf  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "PDF input requires pymupdf. Install with `pip install pymupdf pymupdf4llm` "
            "or convert the PDF to TXT first. See ocr-and-documents skill for scanned PDFs."
        ) from e
    doc = pymupdf.open(str(path))
    out = []
    for page in doc:
        out.append(page.get_text("text"))
    text = "\n".join(out).strip()
    if not text:
        raise RuntimeError(
            f"PDF {path} appears to be scanned/empty — no extractable text. "
            f"Run marker-pdf (see ocr-and-documents skill) or convert to TXT first."
        )
    return text


def _read_docx(path: Path) -> str:
    try:
        import docx  # python-docx  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "DOCX input requires python-docx. `pip install python-docx`"
        ) from e
    d = docx.Document(str(path))
    return "\n".join(p.text for p in d.paragraphs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ingest_input(
    input_path: Optional[Path] = None,
    role: Optional[str] = None,
) -> ReferenceJob:
    """Build a ReferenceJob from a file or a free-text role name.

    Exactly one of ``input_path`` or ``role`` must be provided.
    """
    if (input_path is None) == (role is None):
        raise ValueError("Pass exactly one of `input_path` or `role`")

    notes: list[str] = []

    if role is not None:
        role = role.strip()
        if not role:
            raise ValueError("--role was empty")
        ref = ReferenceJob(
            source="role_name",
            raw_text=role,
            title=extract_title_from_role(role),
            # Use the role string as source_path so the UNIQUE(source, source_path, created_at)
            # constraint catches re-ingest of the exact same role in the same second.
            # (SQLite UNIQUE treats NULLs as distinct, so storing NULL would defeat idempotency.)
            source_path=f"role:{role}",
            role_query=role,
            parse_notes=["input was a free-text role name; no full description"],
        )
        notes = ref.parse_notes
    else:
        p = Path(input_path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(p)
        suffix = p.suffix.lower()
        if suffix == ".pdf":
            text = _read_pdf(p)
            source = "pdf"
        elif suffix == ".docx":
            text = _read_docx(p)
            source = "docx"
        elif suffix in (".txt", ".md", ".markdown", ""):
            text = _read_text(p)
            source = "txt"
        else:
            raise ValueError(
                f"Unsupported input type {suffix!r}. Use .pdf, .docx, .txt, .md — "
                f"or pass --role."
            )
        ref = ReferenceJob(
            source=source,
            raw_text=text,
            source_path=str(p),
            title=extract_title_from_text(text),
            company=extract_company(text),
            location=extract_location(text),
            parse_notes=notes,
        )

    # Universal enrichment (no network)
    ref.language = detect(ref.raw_text)
    ref.skills = extract_skills(ref.raw_text)
    ref.language_queries = language_search_query(ref.raw_text, ref.role_query or ref.title)

    return ref


def ingest_to_db(ref: ReferenceJob, db_path: Optional[Path] = None) -> int:
    """Persist the ReferenceJob to the DB and return its row id.

    Idempotent on (source, source_path, created_at) — same second + same path
    is treated as the same logical input.
    """
    from .. import db as _db  # local import to avoid circulars at module load
    import time
    now = int(time.time())
    with _db.get_conn_ctx(db_path) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO reference_jobs
                (created_at, source, source_path, raw_text, title, company,
                 location, language, skills_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                ref.source,
                ref.source_path,
                ref.raw_text,
                ref.title,
                ref.company,
                ref.location,
                ref.language,
                json.dumps(ref.skills, ensure_ascii=False),
            ),
        )
        if cur.rowcount == 0:
            # Already inserted this exact (source, path, ts) — fetch id
            row = conn.execute(
                "SELECT id FROM reference_jobs WHERE source=? AND source_path=? AND created_at=?",
                (ref.source, ref.source_path, now),
            ).fetchone()
            return int(row["id"])
        return int(cur.lastrowid)
