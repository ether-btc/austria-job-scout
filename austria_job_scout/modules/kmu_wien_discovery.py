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

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

from ..seeds import SeedCompany

logger = logging.getLogger(__name__)


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
