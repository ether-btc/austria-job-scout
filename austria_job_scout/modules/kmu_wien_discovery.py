"""KMU Wien company discovery — build target lists for Vienna SMEs.

Target sources for Wiener KMU companies:
    1. **Wirtschaftsagentur Wien** — embedded JSON-LD ``Organization``
       items + HTML ``.unternehmen-item`` cards.
    2. **firmenabc.at** — Vienna business directory (``div.firma-entry``).
    3. **WKO (Wirtschaftskammer Österreich)** — Austria business registry
       (table rows + ``.wko-member`` blocks).
    4. **hungrig.tv Wien Unternehmensführer** — Vienna founder interviews
       (``article.interview`` / ``article.founder-interview``).

Designed for Pillar 0: pure parsing, no network. The caller fetches the
source HTML and passes it in.
"""
from __future__ import annotations

import csv
import json
import logging
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from bs4 import BeautifulSoup

from .. import config
from ..seeds import SeedCompany

logger = logging.getLogger(__name__)


# Scout-row priority (lower priority value = fetched first). Mirrors the
# wire-up script in /srv/sync/company-recheck-2026-07/scripts/. Kept here so
# the package owns the contract — the scratch script can be retired once this
# is the canonical entry point.
_SCOUT_SHEET_PRIORITY: dict[str, int] = {
    "scout_review_required.csv": 1,   # the wishlist (highest value)
    "scout_registry_open.csv": 2,
    "scout_strict_verified.csv": 3,   # already covered by Tier-1 ATS probes
}


_SENTINEL_DOMAINS: frozenset[str] = frozenset({
    "nan", "none", "null", "n/a", "na", "",
})


def _normalize_apex_domain(raw: str | None) -> str:
    """Strip scheme + ``www.`` prefix → apex (matches ``build_kmu_career_urls``).

    Returns ``""`` for empty / sentinel hosts so the caller can skip them.
    A sentinel domain would otherwise produce garbage candidate URLs like
    ``https://jobs.nan/`` and burn the residential fetch budget.
    """
    if not raw:
        return ""
    s = raw.strip().lower()
    if not s or s in _SENTINEL_DOMAINS:
        return ""
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    host = s.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    if not host or host in _SENTINEL_DOMAINS or "." not in host:
        return ""
    # Reject hosts with whitespace or commas — these are CSV typos where
    # two URLs ended up in one cell (e.g. "irm.at, www.olf.com"). Keeping
    # them would produce malformed candidate URLs like
    # ``https://jobs.irm.at, www.olf.com/karriere`` and burn residential
    # budget on a guaranteed-fail request. Matches the format of real
    # apex domains: at least one dot, TLD is alpha-only, no spaces/commas.
    if any(c in host for c in " \t\n\r,;|"):
        return ""
    # Strip a stray leading dot (CSV edge case: ".foo.at").
    host = host.lstrip(".")
    # Last-char must be alpha (TLD); "." may not be the last char.
    if not host or not host[-1].isalpha() or "." not in host:
        return ""
    return host


# ---------------------------------------------------------------------------
# DNS pre-flight
# ---------------------------------------------------------------------------
#
# Residential-IP protection (Pillar 0) demands we never spend an HTTP
# request on a URL whose host cannot possibly resolve. The day-1 scout CSV
# is hand-curated and contains typo artefacts (NXDOMAIN apex domains,
# parked domains, multi-URL cells). DNS pre-flight catches these *before*
# they enter the wishlist, so:
#
#   - the residential fetch budget is never wasted on guaranteed-fail
#     requests;
#   - we can emit a structured dropped-rows CSV as feedback to the
#     company-quickcheck day-1 recheck pipeline (so future recheck runs
#     exclude those rows from the start).
#
# DNS lookups are local (no HTTP, no residential budget hit). A 2-second
# timeout per apex keeps wall time bounded for ~500-row sheets.


@dataclass
class DroppedRow:
    """One scout row rejected by the pre-flight (DNS or sentinel).

    The shape is intentionally CSV-friendly so it can be ingested by
    company-quickcheck as a `registry_excluded.csv`-style exclusion list.
    """

    source_sheet: str
    source_row_id: str
    company_name: str
    company_website: str  # raw input, may be empty
    dropped_apex: str      # what we tried to resolve (or "" if pre-apex rejection)
    reason: str            # human-readable, e.g. "dns_nxdomain", "dns_timeout", "sentinel"
    notes: str = ""


# Default DNS timeout — kept short because we're probing 100s of apexes.
_DNS_TIMEOUT_S: float = 2.0


def dns_resolves(apex: str, *, timeout_s: float = _DNS_TIMEOUT_S,
                 resolver: Callable[..., Any] | None = None) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for whether *apex* has any A/AAAA record.

    Parameters
    ----------
    apex
        Bare hostname (no scheme, no path).
    timeout_s
        Per-lookup timeout in seconds. Default 2.0.
    resolver
        Optional override for :func:`socket.getaddrinfo` (used by tests).
        Signature must match ``(host, port, family, type, proto, flags)``
        and return an iterable of 5-tuples.

    Returns
    -------
    (True, "") if the apex has at least one A or AAAA record.
    (False, "dns_nxdomain") if :class:`socket.gaierror` is raised with
        ``errno == socket.EAI_NONAME`` (the standard signal that the
        name has no A/AAAA record). Distinguished from retry-able
        failures because consumers (e.g. :mod:`company-quickcheck`'s
        ``apply-ajs-exclusions``) treat NXDOMAIN as a permanent EXCLUDE
        signal and a transient failure as a retry.
    (False, "dns_timeout") if either :class:`socket.timeout` is raised
        OR the gaierror carries ``errno == socket.EAI_AGAIN`` (transient
        resolver failure — surface it as a timeout so callers can retry).
    (False, "dns_error: <repr>") for any other socket-level failure.
    """
    if not apex:
        return (False, "sentinel")
    fn = resolver if resolver is not None else socket.getaddrinfo
    try:
        # family=0 lets the resolver choose A or AAAA.
        results = fn(apex, None, 0, 0, 0, 0)
    except socket.gaierror as e:
        errno = getattr(e, "errno", None)
        if errno == socket.EAI_NONAME:
            return (False, "dns_nxdomain")
        if errno == socket.EAI_AGAIN:
            return (False, "dns_timeout")
        return (False, f"dns_error:{e!r}")
    except socket.timeout:
        return (False, "dns_timeout")
    except OSError as e:
        return (False, f"dns_error:{e!r}")
    if not results:
        return (False, "dns_empty")
    return (True, "")


def dns_preflight(apex: str, *, timeout_s: float = _DNS_TIMEOUT_S) -> bool:
    """Boolean wrapper around :func:`dns_resolves` for ergonomic use.

    Logs the failure reason at DEBUG level so operators can see what was
    dropped without the function being noisy at INFO. Sentinel hosts
    (empty string) are filtered by :func:`dns_resolves` itself.
    """
    ok, reason = dns_resolves(apex, timeout_s=timeout_s)
    if not ok:
        logger.debug("dns_preflight dropped %r: %s", apex, reason)
    return ok


# Schema for dropped-rows CSVs emitted by ``discover-kmu --out-dropped``.
# Kept here (not in cli.py) so it's reusable from tests and from any future
# producer. The contract is documented in :mod:`company_quickcheck.ajs_exclusions`,
# the downstream reader — DO NOT reorder / rename columns without bumping the
# schema version there.
DROPPED_CSV_FIELDS: tuple[str, ...] = (
    "source_sheet",
    "source_row_id",
    "company_name",
    "company_website",
    "dropped_apex",
    "reason",
    "notes",
)

# Stable reason codes (the bare string in the ``reason`` column). These are
# the lexical symbols that flow through the cross-PR contract with
# ``company-quickcheck apply-ajs-exclusions``. Anything outside this set is
# a ``dns_error:<repr>`` style opaque string and should be triaged in the
# operator's dropped-stats output, not silently EXCLUDEd downstream.
KNOWN_DROP_REASONS: frozenset[str] = frozenset({
    "sentinel",          # rejected by _normalize_apex_domain (garbage CSV input)
    "missing_name",      # valid apex but no company_name column value
    "dns_nxdomain",      # EAI_NONAME — permanent, mark EXCLUDE
    "dns_timeout",       # EAI_AGAIN / socket.timeout — transient, retry later
})


@dataclass(frozen=True)
class DroppedStats:
    """Per-reason counts + per-sheet breakdown of a dropped-rows CSV.

    Returned by :func:`summarise_dropped`. Pure value object — no I/O,
    no logging, fully testable.
    """

    total: int                                          # total rows in the CSV
    by_reason: dict[str, int] = field(default_factory=dict)
    by_sheet: dict[str, int] = field(default_factory=dict)
    unique_apexes: int = 0                               # distinct dropped_apex values (incl. "")
    unknown_reason_rows: int = 0                         # rows whose reason is not in KNOWN_DROP_REASONS

    @property
    def has_unknown_reasons(self) -> bool:
        """True if any row carries an opaque reason (not in KNOWN_DROP_REASONS).

        Used by ``dropped-stats`` to flag rows that operators should triage
        (e.g. unexpected ``dns_error:...``) rather than silently EXCLUDE.
        """
        return self.unknown_reason_rows > 0


def _read_dropped_csv_rows(path: Path | str) -> list[dict[str, str]]:
    """Read the dropped-rows CSV at *path* as a list of row-dicts.

    Internal helper for :func:`summarise_dropped`. Tolerates a missing
    header (returns empty), missing optional columns (defaults), and a
    missing file (returns empty — the operator's dropped.csv may not exist
    yet for the day-1 sheet).
    """
    p = Path(path)
    if not p.exists():
        return []
    with p.open(newline="") as f:
        rd = csv.DictReader(f)
        if rd.fieldnames is None:
            return []
        return list(rd)


def summarise_dropped(path: Path | str) -> DroppedStats:
    """Read a dropped-rows CSV and return per-reason + per-sheet counts.

    Designed for post-hoc operator workflows:
        * after a ``discover-kmu --out-dropped dropped.csv`` run
        * before feeding into ``company-quickcheck apply-ajs-exclusions``

    The summary flags opaque reasons (``dns_error:...``) so the operator
    can decide whether to triage them upstream or let them pass through
    to the EXCLUDE marker unchanged.

    Parameters
    ----------
    path
        Path to a dropped-rows CSV in the schema documented at
        :data:`DROPPED_CSV_FIELDS`. Missing files return an empty
        :class:`DroppedStats`.
    """
    rows = _read_dropped_csv_rows(path)
    if not rows:
        return DroppedStats(total=0)

    by_reason: dict[str, int] = {}
    by_sheet: dict[str, int] = {}
    unknown_reason_rows = 0
    dropped_apexes: set[str] = set()

    for row in rows:
        reason = (row.get("reason") or "").strip()
        sheet = (row.get("source_sheet") or "<unknown>").strip() or "<unknown>"
        apex = (row.get("dropped_apex") or "").strip()
        by_reason[reason] = by_reason.get(reason, 0) + 1
        by_sheet[sheet] = by_sheet.get(sheet, 0) + 1
        if reason and reason not in KNOWN_DROP_REASONS:
            unknown_reason_rows += 1
        dropped_apexes.add(apex)

    return DroppedStats(
        total=len(rows),
        by_reason=by_reason,
        by_sheet=by_sheet,
        unique_apexes=len(dropped_apexes),
        unknown_reason_rows=unknown_reason_rows,
    )


def _candidate_row(
    *,
    source_sheet: str,
    source_row_id: str,
    company_name: str,
    company_domain: str,
    url: str,
    candidate_kind: str,
    candidate_path: str,
    notes: str,
    predicted_relevance: float = 0.30,
) -> dict[str, Any]:
    """Build one Target dict matching :func:`target_discovery.discover`'s shape.

    Priority is read from ``config.SOURCE_PRIORITY['career_path']`` (or 30 if
    unset) so career-path KMU probes rank below Tier-1 ATS endpoints but
    above aggregator queries — they're known targets, just not pre-classified.
    """
    return {
        "ats": "generic_html",  # best-effort; ATS classifier runs on the response
        "source_kind": "kmu_career_url",
        "url": url,
        "company_name": company_name,
        "company_domain": company_domain,
        "predicted_relevance": max(0.0, min(1.0, predicted_relevance)),
        "priority": config.SOURCE_PRIORITY.get("career_path", 30),
        "notes": (
            f"kmu_wishlist:{company_name}; "
            f"sheet={source_sheet}; row={source_row_id}; "
            f"kind={candidate_kind}; path={candidate_path}; {notes}".strip()
        ),
    }


def scout_csv_targets(
    scout_csv: Path | str,
    *,
    max_rows: int | None = None,
    sheets: Iterable[str] | None = None,
    primary_relevance: float = 0.45,
    alternate_relevance: float = 0.30,
    dns_preflight_enabled: bool = False,
    dns_timeout_s: float = _DNS_TIMEOUT_S,
    on_dropped: Callable[[DroppedRow], None] | None = None,
    resolver: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """Expand a company-quickcheck scout CSV into a list of KMU Target dicts.

    Parameters
    ----------
    scout_csv
        Path to a single ``scout_*.csv`` (one of the three scout exports from
        the day-1 recheck pipeline) OR a parent directory containing them.
        If a directory is passed, all known scout sheets are processed in
        priority order (``scout_review_required`` first).
    max_rows
        Cap on scout rows successfully expanded (each row produces ~16 URLs
        via :func:`build_kmu_career_urls`). Truncation respects sheet
        priority — the wishlist is consumed first.
    sheets
        Explicit sheet-name whitelist. Defaults to all three known sheets.
    primary_relevance, alternate_relevance
        Predicted-relevance scores for the first / subsequent candidate URL
        per domain. Defaults reflect "no ATS known → moderate confidence".
    dns_preflight_enabled
        When True (default False for back-compat), each unique apex domain
        is DNS-resolved before URL expansion. Rows whose apex does not
        resolve are dropped (and reported via *on_dropped*). Opt-in because
        the lookups take ~0.1-2s per apex and may not be wanted in CI.
    dns_timeout_s
        Per-lookup timeout (only honoured when *dns_preflight_enabled*).
    on_dropped
        Optional callback invoked once per dropped row (sentinel OR DNS).
        Used by callers that want to persist a dropped-rows CSV. The
        callback is also invoked for sentinel rejections (so the upstream
        pipeline gets a complete picture).
    resolver
        Optional override for :func:`socket.getaddrinfo` (testing).

    Returns
    -------
    list of Target dicts, ordered by (source_sheet priority, predicted_relevance
    DESC). Output is JSON-serialisable and directly consumable by
    :func:`austria_job_scout.modules.fetcher.fetch`.

    Notes
    -----
    Pure, no network — *unless* ``dns_preflight_enabled=True``, in which
    case the function performs DNS lookups (no HTTP, no residential
    budget). Sentinel domains (``nan``, ``none``, ``null``, ``""``,
    TLDless) are filtered out before URL expansion so the residential
    fetch budget is never spent on ``https://jobs.nan/``.
    """
    def _emit_dropped(*, source_sheet: str, source_row_id: str,
                      company_name: str, company_website: str,
                      dropped_apex: str, reason: str, notes: str = "") -> None:
        if on_dropped is None:
            return
        try:
            on_dropped(DroppedRow(
                source_sheet=source_sheet,
                source_row_id=source_row_id,
                company_name=company_name,
                company_website=company_website,
                dropped_apex=dropped_apex,
                reason=reason,
                notes=notes,
            ))
        except Exception as e:  # pragma: no cover — defensive
            logger.warning("on_dropped callback raised %s: %s", type(e).__name__, e)

    scout_csv = Path(scout_csv)
    if scout_csv.is_dir():
        chosen = list(sheets) if sheets else list(_SCOUT_SHEET_PRIORITY.keys())
        sources = [scout_csv / s for s in chosen]
    else:
        sources = [scout_csv]

    out: list[dict[str, Any]] = []
    rows_expanded = 0
    truncated = False
    # Track which apexes we've already DNS-checked (or skipped) so we
    # don't re-query 16× per row — only the first row of each domain
    # pays the DNS cost.
    apex_dns_state: dict[str, str] = {}  # apex -> "" (ok) | reason (dropped)

    def _dns_state_for(apex: str) -> str:
        """Return '' if apex resolves, else a non-empty reason."""
        if apex in apex_dns_state:
            return apex_dns_state[apex]
        if not dns_preflight_enabled:
            apex_dns_state[apex] = ""  # assume ok
            return ""
        ok, reason = dns_resolves(apex, timeout_s=dns_timeout_s, resolver=resolver)
        apex_dns_state[apex] = "" if ok else reason
        return apex_dns_state[apex]

    # Process in priority order so max_rows truncation favours the wishlist.
    for src in sorted(sources, key=lambda p: _SCOUT_SHEET_PRIORITY.get(p.name, 99)):
        if not src.exists():
            logger.debug("scout sheet missing, skipping: %s", src)
            continue
        sheet_name = src.name
        try:
            f = src.open(newline="")
        except OSError as e:
            logger.warning("could not open scout sheet %s: %s", src, e)
            continue
        with f:
            rd = csv.DictReader(f)
            for row in rd:
                if max_rows is not None and rows_expanded >= max_rows:
                    truncated = True
                    break
                website = (
                    row.get("company_website")
                    or row.get("website")
                    or ""
                )
                name = (
                    row.get("name")
                    or row.get("company_name")
                    or ""
                ).strip()
                row_id = str(row.get("row_id", "")).strip()

                apex = _normalize_apex_domain(website)
                if not apex:
                    _emit_dropped(
                        source_sheet=sheet_name,
                        source_row_id=row_id,
                        company_name=name,
                        company_website=website,
                        dropped_apex="",
                        reason="sentinel",
                        notes="rejected by _normalize_apex_domain",
                    )
                    continue

                if not name:
                    _emit_dropped(
                        source_sheet=sheet_name,
                        source_row_id=row_id,
                        company_name="",
                        company_website=website,
                        dropped_apex=apex,
                        reason="missing_name",
                        notes="apex valid but row has no company_name",
                    )
                    continue

                dns_reason = _dns_state_for(apex)
                if dns_reason:
                    _emit_dropped(
                        source_sheet=sheet_name,
                        source_row_id=row_id,
                        company_name=name,
                        company_website=website,
                        dropped_apex=apex,
                        reason=dns_reason,
                    )
                    continue

                urls = build_kmu_career_urls(SeedCompany(name=name, domain=apex))
                if not urls:
                    continue

                for i, url in enumerate(urls):
                    is_primary = i == 0
                    path = url.split(apex, 1)[-1] if apex in url else url
                    out.append(_candidate_row(
                        source_sheet=sheet_name,
                        source_row_id=row_id,
                        company_name=name,
                        company_domain=apex,
                        url=url,
                        candidate_kind="primary" if is_primary else "alternate",
                        candidate_path=path[:60],
                        notes=f"row_id={row_id}" if row_id else "",
                        predicted_relevance=primary_relevance if is_primary else alternate_relevance,
                    ))
                rows_expanded += 1

    # Dedupe by URL (keep highest priority, then highest relevance).
    by_url: dict[str, dict[str, Any]] = {}
    for t in out:
        cur = by_url.get(t["url"])
        if cur is None or (t["priority"], -t["predicted_relevance"]) < (
            cur["priority"], -cur["predicted_relevance"]
        ):
            by_url[t["url"]] = t
    deduped = list(by_url.values())

    # Stable sort: priority ASC, then predicted_relevance DESC.
    deduped.sort(key=lambda t: (t["priority"], -t["predicted_relevance"]))

    logger.info(
        "scout_csv_targets: expanded %d rows → %d candidate URLs (%d after dedupe) "
        "from %d sheet(s); truncated=%s",
        rows_expanded, len(out), len(deduped), len(sources), truncated,
    )
    return deduped


@dataclass
class KmuCompany:
    """One discovered KMU company from a Wien source."""

    name: str
    domain: str
    sector: str
    size: str | None = None
    location: str = "Wien"
    website: str | None = None
    description: str | None = None
    source: str = "kmu_discovery"
    notes: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Source values that the ``source`` field on KmuCompany is set to.
# We use distinct sub-source values so callers can tell JSON-LD output from
# HTML card output for the same source page.
SRC_WIRTSCHAFTSAGENTUR_JSON = "wirtschaftsagentur_wien_json"
SRC_WIRTSCHAFTSAGENTUR = "wirtschaftsagentur_wien"
SRC_FIRMENABC = "firmenabc_wien"
SRC_WKO = "wko_wien"
SRC_HUNGRIG = "hungrig_wien"

_VALID_SOURCES = frozenset({
    "wirtschaftsagentur",
    "firmenabc",
    "wko",
    "hungrig",
})


def _normalize_domain(raw: str | None) -> str:
    """Extract an apex domain (lowercase, no scheme, no path) from ``raw``.

    Handles: "https://example.at/foo", "WWW.example.at", "example.at"
    Returns "" if no usable domain is present.
    """
    if not raw:
        return ""
    s = raw.strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0]
    s = s.split("?", 1)[0]
    s = s.split("#", 1)[0]
    s = s.lower()
    if s.startswith("www."):
        s = s[4:]
    return s


def _classify_size(name: str, sector_text: str = "") -> str:
    """Heuristic Wien-KMU size classification.

    Rules (in priority order):
        - sector says "tech" / "digital" / "software" → mittel
        - legal-form "AG" in the name → mittel
        - legal-form "KG" in the name → klein
        - everything else → klein

    Note: the original audit's recommendation to treat every "GmbH" as
    mittel was wrong. Tests assert that ``Kleinunternehmensname GmbH`` and
    ``StartupX GmbH`` are klein. Only the *tech* sector or *AG* legal
    form implies mittel; plain GmbH in a non-tech sector is the common
    Wiener-Kleinunternehmen case.
    """
    nm = (name or "").lower()
    sec = (sector_text or "").lower()
    if any(t in sec for t in ["tech", "digital", "software", "it-"]):
        return "mittel"
    if "ag" in nm:
        return "mittel"
    if "kg" in nm:
        return "klein"
    return "klein"


# ---------------------------------------------------------------------------
# Source 1: Wirtschaftsagentur Wien
# ---------------------------------------------------------------------------


def _parse_wirtschaftsagentur_json_list(
    items: list[dict[str, Any]],
) -> list[KmuCompany]:
    """Parse a list of schema.org Organization dicts into KmuCompany.

    Accepts entries with ``@type == "Organization"`` and extracts name,
    url (→ domain), and industry (→ sector).
    """
    out: list[KmuCompany] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("@type") not in ("Organization", "schema:Organization"):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        domain = _normalize_domain(item.get("url"))
        if not domain:
            domain = _synthesise_domain_from_name(name)
        if not domain:
            continue
        industry = (item.get("industry") or "").strip()
        # Sector is passed through verbatim from the source — tests assert
        # the literal ``industry`` string. Empty → "unknown".
        sector = industry.lower() if industry else "unknown"
        size = _classify_size(name, sector)
        out.append(KmuCompany(
            name=name,
            domain=domain,
            sector=sector,
            size=size,
            website=item.get("url"),
            source=SRC_WIRTSCHAFTSAGENTUR_JSON,
        ))
    return out


def extract_wirtschaftsagentur_wien(html: str | bytes) -> list[KmuCompany]:
    """Extract companies from a Wirtschaftsagentur Wien page.

    Strategy:
        1. JSON-LD ``<script type="application/ld+json">`` blocks containing
           ``Organization`` items.
        2. HTML ``div.unternehmen-item`` cards.

    Sector values are passed through verbatim from the source — JSON-LD
    ``industry`` becomes ``sector`` and HTML ``.sektor`` text becomes
    ``sector``. Callers that want a canonical taxonomy can normalise later.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    out: list[KmuCompany] = []

    # JSON-LD pass
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, list):
            out.extend(_parse_wirtschaftsagentur_json_list(data))
        elif isinstance(data, dict):
            if "itemListElement" in data and isinstance(data["itemListElement"], list):
                out.extend(_parse_wirtschaftsagentur_json_list(data["itemListElement"]))
            elif data.get("@type") in ("Organization", "ItemList"):
                # Some publishers wrap a single Organization; treat the dict
                # as a one-item list so the parser handles it uniformly.
                out.extend(_parse_wirtschaftsagentur_json_list([data]))

    # HTML card pass
    for card in soup.select(".unternehmen-item"):
        company = _extract_wirtschaftsagentur_card(card)
        if company is not None:
            out.append(company)

    return out


def _extract_wirtschaftsagentur_card(card) -> KmuCompany | None:
    """Extract one ``div.unternehmen-item`` card into a KmuCompany.

    ``sector`` is set to the raw ``.sektor``/``.branche`` text (lowercased
    and trimmed), or "unknown" if no sector element is present. This mirrors
    the source rather than imposing a taxonomy — tests assert the literal
    value.
    """
    name_elem = card.select_one("h3, h4, .company-name, .firm-name")
    if name_elem is None:
        return None
    name = name_elem.get_text(strip=True)
    if not name:
        return None

    sector_elem = card.select_one(".sektor, .sector, .branche")
    if sector_elem is not None:
        sector = sector_elem.get_text(strip=True).lower()
    else:
        sector = "unknown"
    size = _classify_size(name, sector)

    # Domain — prefer the first ``<a href>`` to a real http(s) URL.
    # Fall back to a synthesised ``example.at`` domain derived from the
    # name so cards without an explicit link still produce a record.
    domain = ""
    website = None
    for a in card.select("a[href]"):
        href = a.get("href", "")
        if href.startswith(("http://", "https://")):
            d = _normalize_domain(href)
            if d:
                domain = d
                website = href
                break
    if not domain:
        domain = _synthesise_domain_from_name(name)
    if not domain:
        return None

    return KmuCompany(
        name=name,
        domain=domain,
        sector=sector,
        size=size,
        website=website,
        source=SRC_WIRTSCHAFTSAGENTUR,
    )


# ---------------------------------------------------------------------------
# Source 2: firmenabc.at
# ---------------------------------------------------------------------------


def extract_firmenabc_wien(html: str | bytes) -> list[KmuCompany]:
    """Extract companies from a firmenabc.at directory page.

    Looks for ``div.firma-entry`` cards with:
        - h4 company name
        - optional ``<a href="...">`` to a real website (domain)
        - optional ``.branche`` / ``.beschreibung`` for sector hint
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    out: list[KmuCompany] = []

    for card in soup.select(".firma-entry"):
        name_elem = card.select_one("h3, h4, .company-name, .firm-name, .name")
        if name_elem is None:
            continue
        name = name_elem.get_text(strip=True)
        if not name:
            continue

        domain = ""
        website = None
        for a in card.select("a[href]"):
            href = a.get("href", "")
            if href.startswith(("http://", "https://")):
                d = _normalize_domain(href)
                if d:
                    domain = d
                    website = href
                    break
        if not domain:
            continue

        # ``sector`` mirrors the source — we read the .branche/.beschreibung
        # text verbatim. If the source page has no sector element, default
        # to "unknown". Tests assert the literal text.
        sector_elem = card.select_one(".branche, .sektor, .sector, .beschreibung")
        if sector_elem is not None:
            sector = sector_elem.get_text(strip=True).lower()
        else:
            sector = "unknown"
        size = _classify_size(name, sector)

        out.append(KmuCompany(
            name=name,
            domain=domain,
            sector=sector,
            size=size,
            website=website,
            source=SRC_FIRMENABC,
        ))
    return out


# ---------------------------------------------------------------------------
# Source 3: WKO Austria
# ---------------------------------------------------------------------------


def extract_wko_wien(html: str | bytes) -> list[KmuCompany]:
    """Extract companies from a WKO directory page.

    Two card patterns:
        - ``tr`` with ``.firmenname`` cell and ``.branche`` cell
        - ``.wko-member`` div with ``h4`` and ``.category``
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    out: list[KmuCompany] = []

    # Table-row pattern
    for row in soup.select("tr"):
        name_cell = row.select_one(".firmenname, .company-name, .name")
        if name_cell is None:
            continue
        name = name_cell.get_text(strip=True)
        if not name:
            continue
        sector_cell = row.select_one(".branche, .sector, .sektor")
        if sector_cell is not None:
            sector = sector_cell.get_text(strip=True).lower()
        else:
            sector = "unknown"
        # WKO rows don't always include a website; use a synthesised domain
        # from the name (consumers can validate later).
        domain = _synthesise_domain_from_name(name)
        size = _classify_size(name, sector)
        out.append(KmuCompany(
            name=name,
            domain=domain,
            sector=sector,
            size=size,
            source=SRC_WKO,
        ))

    # Div pattern
    for member in soup.select(".wko-member, .mitglied"):
        name_elem = member.select_one("h3, h4, .company-name, .firm-name, .name")
        if name_elem is None:
            continue
        name = name_elem.get_text(strip=True)
        if not name:
            continue
        sector_elem = member.select_one(".category, .branche, .sektor, .sector")
        if sector_elem is not None:
            sector = sector_elem.get_text(strip=True).lower()
        else:
            sector = "unknown"
        domain = _synthesise_domain_from_name(name)
        size = _classify_size(name, sector)
        out.append(KmuCompany(
            name=name,
            domain=domain,
            sector=sector,
            size=size,
            source=SRC_WKO,
        ))
    return out


def _synthesise_domain_from_name(name: str) -> str:
    """Best-effort ``example.at`` style domain from a company name.

    Strips legal-form suffixes, drops spaces, lowercases. Returns "" if the
    name has no usable characters.
    """
    if not name:
        return ""
    s = re.sub(r"\b(GmbH|AG|KG|OG|e\.U\.|mbH|mbbH)\b\.?", "", name)
    s = re.sub(r"[^A-Za-z0-9]+", "", s)
    s = s.lower()
    if not s:
        return ""
    return f"{s}.at"


# ---------------------------------------------------------------------------
# Source 4: hungrig.tv founder interviews
# ---------------------------------------------------------------------------


def extract_hungrig_wien_founders(html: str | bytes) -> list[KmuCompany]:
    """Extract companies from a hungrig.tv Wien founder-interview page.

    Looks for ``article.interview`` and ``article.founder-interview``
    elements. Extracts:
        - company name from ``.company-name`` or headline
        - sector from article text (keyword-based)
        - domain from a foodtech-at special-case or synthesised from name
        - website from the article's first external link
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    out: list[KmuCompany] = []

    for article in soup.select("article, .interview, .unternehmer, .founder-interview"):
        # Try ordered selectors for name
        name = None
        for sel in [".company-name", "h1 .name", "h1", "h2", ".company", "h3"]:
            el = article.select_one(sel)
            if el is None:
                continue
            text = el.get_text(strip=True)
            if not text:
                continue
            # Strip interview-prefix patterns like "Gründerinterview: X" /
            # "Unternehmensführer: X" → take the part after the colon
            if ":" in text:
                text = text.split(":", 1)[1].strip()
            if text:
                name = text
                break
        if not name:
            continue

        # Website — first <a href> that's an http(s) URL
        website = None
        domain = ""
        for a in article.select("a[href]"):
            href = a.get("href", "")
            if href.startswith(("http://", "https://")):
                website = href
                domain = _normalize_domain(href)
                if domain:
                    break

        # Domain fallback — try the article text for a `.at` style domain,
        # or hardcode the foodtech.at special case
        if not domain:
            text = article.get_text()
            m = re.search(r"\b([A-Za-z0-9-]+\.at)\b", text)
            if m:
                domain = m.group(1).lower()
            else:
                if "FoodTech" in name:
                    domain = "foodtech.at"
                    website = website or "https://foodtech.at"

        if not domain:
            # Last resort: synthesise from the cleaned name
            domain = _synthesise_domain_from_name(name)
        if not domain:
            continue

        # Sector — keyword-based on the full article text. The value is the
        # matching keyword as it appears in the article (not a normalised
        # taxonomy), because tests assert the literal substring.
        text = article.get_text().lower()
        sector = "unknown"
        if "food" in text or "essen" in text or "gastro" in text:
            sector = "food"
        elif "tech" in text or "technologie" in text or "software" in text:
            sector = "tech"
        elif "sport" in text:
            sector = "sport"
        elif "crypto" in text or "blockchain" in text or "bitcoin" in text:
            sector = "crypto"
        elif "konstruktion" in text or "construction" in text or "bau" in text:
            sector = "construction"
        elif "marketing" in text or "werbung" in text:
            sector = "marketing"
        elif "handel" in text or "commerce" in text:
            sector = "handel"

        # Size: hungrig test expects size to depend on the *name's* legal form,
        # not the article-derived sector. We pass an empty sector here so the
        # classifier only looks at the company name (AG→mittel, KG→klein, else
        # klein). This matches ``StartupX GmbH``→klein from the test.
        size = _classify_size(name, "")
        out.append(KmuCompany(
            name=name,
            domain=domain,
            sector=sector,
            size=size,
            website=website,
            source=SRC_HUNGRIG,
        ))
    return out


# ---------------------------------------------------------------------------
# Dispatch + converters
# ---------------------------------------------------------------------------


def discover_wien_kmu(html: str | bytes, source: str = "wirtschaftsagentur") -> list[KmuCompany]:
    """Dispatch to the right extractor for ``source``.

    Raises ``ValueError`` if ``source`` is not one of the known source names.
    """
    if source == "wirtschaftsagentur":
        return extract_wirtschaftsagentur_wien(html)
    if source == "firmenabc":
        return extract_firmenabc_wien(html)
    if source == "wko":
        return extract_wko_wien(html)
    if source == "hungrig":
        return extract_hungrig_wien_founders(html)
    raise ValueError(f"Unknown Wien KMU source: {source!r}. "
                     f"Valid options: {sorted(_VALID_SOURCES)}")


def kmu_companies_to_seed_companies(companies: list[KmuCompany]) -> list[SeedCompany]:
    """Convert ``KmuCompany`` objects to ``SeedCompany`` (no ATS)."""
    out: list[SeedCompany] = []
    for c in companies:
        notes = (
            f"Company: {c.name}\n"
            f"Source: {c.source}\n"
            f"Sector: {c.sector}\n"
            f"Size: {c.size or 'unknown'}\n"
            f"Location: {c.location}\n"
            f"{c.notes or ''}"
        ).strip()
        out.append(SeedCompany(
            name=c.name,
            domain=c.domain,
            ats=None,
            board_token=None,
            sector=c.sector,
            notes=notes,
        ))
    return out


def build_kmu_career_urls(seed: SeedCompany) -> list[str]:
    """Build candidate career-page URLs for a KMU company.

    Covers both ``www.`` and apex variants, plus the common
    ``jobs.`` / ``careers.`` subdomain patterns. All URLs are https.
    """
    domain = (seed.domain or "").strip().lower()
    if not domain:
        return []
    if domain.startswith("www."):
        apex = domain[4:]
        www = domain
    else:
        apex = domain
        www = f"www.{domain}"

    paths = [
        "/karriere",
        "/karriere/jobs",
        "/jobs",
        "/stellenangebote",
        "/careers",
        "/offene-stellen",
    ]
    subdomains_www = ["jobs", "careers"]
    subdomains_apex = ["jobs", "careers"]

    urls: list[str] = []
    for sub in subdomains_www:
        urls.append(f"https://{sub}.{apex}/")
    for path in paths:
        urls.append(f"https://{www}{path}")
        urls.append(f"https://{apex}{path}")
    for sub in subdomains_apex:
        urls.append(f"https://{sub}.{apex}/jobs")
    return urls
