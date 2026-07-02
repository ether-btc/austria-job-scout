"""KMU Wien company discovery — build target lists for Vienna SMEs.

Target sources for Wiener KMU companies:
    1. **Wirtschaftsagentur Wien** — curated Wien SME list
    2. **firmenabc.at** — Vienna business directory
    3. **WKO (Wirtschaftskammer Österreich)** — Austria business registry
    4. **hungrig.tv Wien Unternehmensführer** — Vienna founder interviews

    Each source provides company names, websites, and some metadata (sector, size)
    that can be used for relevance scoring. This module parses those sources
    into SeedCompany objects that the orchestrator can use.

    Designed to be Pillar 0-compliant: pure function, no network. The caller
    is responsible for fetching the source content.
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
        name: str                        # Canonical company name
        domain: str                      # Website apex domain (lowercase, no scheme)
        sector: str                      # Business sector classification
        size: str | None = None           # "klein", "mittel", "gross"
        location: str = "Wien"          # Always Wien for this module
        website: str | None = None       # Full URL (if available)
        description: str | None = None   # Company description
        source: str = "kmu_discovery"   # Source provenance
        notes: str = ""                 # Additional context


        # ---------------------------------------------------------------------------
        # Wien KMU sources
        # ---------------------------------------------------------------------------


        def _extract_wirtschaftsagentur_sectors_and_size(company_name: str, sector_text: str) -> tuple[str, str]:
            """Extract sector and size classification for Wirtschaftsagentur Wien companies."""
            sector = "unknown"
            if sector_text:
            sector = sector_text.lower()

            # Wien SME classification (guess based on name/sector)
            size = "klein"  # Default most common

            if any(suffix in company_name.lower() for suffix in ["ag", "gmbh", "kg"]):
            size = "mittel"
            elif "holding" in company_name.lower() or any(term in company_name.lower() for term in ["groß", "gross", "major"]):
            size = "gross"
            elif "tech" in sector or "digital" in sector or company_name.lower() in ("bitpanda", "refurbed"):
            size = "mittel"
            elif company_name in ["Siemens", "IBM"] or "gross" in sector:
            size = "gross"

            # Sector classification
            if "handel" in sector or "commerce" in sector:
            sector = "commerce"
            elif "industrie" in sector or "manufacturing" in sector:
            sector = "manufacturing"
            elif "dienstleistung" in sector or "service" in sector:
            sector = "services"
            elif "tech" in sector or "digital" in sector:
            sector = "technology"
            elif "bank" in sector or "finance" in sector:
            sector = "finance"

            return sector, size


            def extract_wirtschaftsagentur_wien(html: str | bytes) -> list[KmuCompany]:
                """Extract companies from Wirtschaftsagentur Wien "Unternehmen" section.

                Source: https://www.wirtschaftsagentur.at/unternehmen/
                Typically embedded JSON in HTML or structured lists.
                """
                soup = BeautifulSoup(html, "lxml")
                companies: list[KmuCompany] = []

                # Pattern 1: Look for embedded JSON (common pattern)
                json_scripts = soup.find_all("script", type="application/ld+json")
                for script in json_scripts:
                    try:
                        data = json.loads(script.string or "{}")
                        if isinstance(data, list):
                        companies.extend(_parse_wirtschaftsagentur_json_list(data))
                        elif isinstance(data, dict) and "itemListElement" in data:
                        companies.extend(_parse_wirtschaftsagentur_json_list(data.get("itemListElement", [])))
                        except json.JSONDecodeError:
                            continue

                            # Pattern 2: Manual HTML parsing of company cards
                            if not companies:
                            # More robust selectors for different HTML structures
                            for card in company_cards:
                                company = _extract_wirtschaftsagentur_card(card)
                                if company:
                                companies.append(company)

                                return companies


                                def _extract_wirtschaftsagentur_card(card) -> KmuCompany | None:
                                    """Extract one company card from Wirtschaftsagentur Wien listing."""
                                    # More flexible name selectors
                                    name_elem = (
                                    card.select_one("h1, h2, h3, h4, h5, .company-name, .firmenname, .firma-name, .unternehmen-name") or
                                    card.select_one(":has(h1), :has(h2), :has(h3), :has(h4)") or
                                    card.select_one("div:first-child")
                                    )
                                    if not name_elem:
                                    return None

                                    name = name_elem.get_text(strip=True)
                                    if len(name) < 3:
                                    return None

                                    url_elem = card.select_one("a[href*='//'], .website-link")
                                    sector_elem = card.select_one(".sektor, .branche, .brancheninfo")

                                    # Extract domain from link or guess from name
                                    href = url_elem.get("href", "") if url_elem else ""
                                    if href:
                                    match = re.search(r"https?://([^/]+)", href)
                                    domain = match.group(1).lower() if match else ""
                                    else:
                                    domain = f"{name.lower().replace(' ', '-')}.at"

                                    # Get sector and size
                                    sector_text = sector_elem.get_text(strip=True) if sector_elem else ""
                                    sector, size = _extract_wirtschaftsagentur_sectors_and_size(name, sector_text)

                                    return KmuCompany(
                                    name=name,
                                    domain=domain,
                                    sector=sector,
                                    size=size,
                                    website=href if href else None,
                                    source="wirtschaftsagentur_wien",
                                    notes=f"Found in listing",
                                    )


                                    def extract_firmenabc_wien(html: str | bytes) -> list[KmuCompany]:
                                        """Extract companies from firmenabc.at Vienna business directory.

                                        Source: https://firmenabc.at/wien/ or similar Wien directory pages.
                                        Extracts company names and from listing pages, then reconstructs URLs.
                                        """
                                        soup = BeautifulSoup(html, "lxml")

                                        companies: list[KmuCompany] = []

                                        # Pattern: business directory listing entries
                                        # Common selectors for firmenabc.at style directories
                                        entries = soup.select(
                                        ".firma-entry, .firmenliste .firma, .company-entry, .business-listing li, .directory-item"
                                        )

                                        for entry in entries:
                                            # Company name - multiple patterns
                                            name_elem = entry.select_one("h3, h4, .firma-name, .company-name, .firmenname")
                                            if not name_elem:
                                            name_elem = entry.select_one("a:has(h3), a:has(h4)")

                                            if not name_elem:
                                            continue

                                            name = name_elem.get_text(strip=True)
                                            if len(name) < 3:
                                            continue

                                            # Extract website link
                                            url_elem = entry.select_one("a[href*='://']")
                                            website = url_elem.get("href", "") if url_elem else None

                                            # Extract sector/description
                                            desc_elem = entry.select_one(".beschreibung, .branche, .category, .sector")
                                            sector = "unknown"
                                            if desc_elem:
                                            sector = desc_elem.get_text(strip=True).lower()

                                            # Extract domain from website URL or reconstruct
                                            domain = ""
                                            if website:
                                            match = re.search(r"https?://([^/]+)", website)
                                            if match:
                                            domain = match.group(1).lower()
                                            else:
                                            # Reconstruct domain from company name
                                            domain = f"{name.lower().replace(' ', '-')}.at"

                                            # Size classification for Wien firms
                                            size = "klein"
                                            if name.lower() in (["bit", "byte", "digital", "tech", "software"]):
                                            size = "mittel"
                                            elif "ag" in name.lower() or "gmbh" in name.lower():
                                            size = "mittel"
                                            elif "holding" in name.lower():
                                            size = "gross"

                                            companies.append(KmuCompany(
                                            name=name,
                                            domain=domain,
                                            sector=sector,
                                            size=size,
                                            website=website,
                                            source="firmenabc_wien",
                                            notes=f"Wien business directory",
                                            ))

                                            return companies


                                            def extract_wko_wien(html: str | bytes) -> list[KmuCompany]:
                                                """Extract Wien KMU from WKO (Wirtschaftskammer Österreich) listings.

                                                Source: WKO Wien business registry listings. Typically structured data.
                                                """
                                                soup = BeautifulSoup(html, "lxml")

                                                companies: list[KmuCompany] = []

                                                # Pattern: WKO often has structured company data in tables or divs
                                                entries = soup.select(
                                                ".firma, .unternehmen, .mitglied, .wko-member, .member-entry",
                                                "table.firmenliste tr:not(:first-child)",
                                                ".company-item"
                                                )

                                                for entry in entries:
                                                    # Company name
                                                    name_elem = entry.select_one("h3, h4, .firmenname, .company-name, .name")
                                                    if not name_elem:
                                                    continue

                                                    name = name_elem.get_text(strip=True)
                                                    if len(name) < 3:
                                                    continue

                                                    # Website link
                                                    website_elem = entry.select_one("a[href*='://']")
                                                    website = website_elem.get("href", "") if website_elem else None

                                                    # Extract domain
                                                    domain = ""
                                                    if website:
                                                    match = re.search(r"https?://([^/]+)", str(website))
                                                    if match:
                                                    domain = match.group(1).lower()
                                                    else:
                                                    # WKO companies often have .at domains
                                                    domain = f"{name.lower().replace(' ', '-')}.at"

                                                    # Sector classification (guess from WKO section names)
                                                    sector = "unknown"
                                                    parent = entry.find_parent()
                                                    if parent:
                                                    title = parent.get_text(strip=True).lower()
                                                    if "handel" in title or "commerce" in title:
                                                    sector = "commerce"
                                                    elif "industrie" in title or "manufacturing" in title:
                                                    sector = "manufacturing"
                                                    elif "dienstleistung" in title or "service" in title:
                                                    sector = "services"
                                                    elif "tech" in title or "digital" in title:
                                                    sector = "technology"
                                                    elif "bank" in title or "finance" in title:
                                                    sector = "finance"

                                                    # WKO member classification by name conventions
                                                    size = "klein"
                                                    if any(suffix in name.lower() for suffix in ["ag", "gmbh", "kg"]):
                                                    size = "mittel"
                                                    elif "holding" in name.lower() or any(term in name.lower() for term in ["groß", "gross", "major"]):
                                                    size = "gross"

                                                    companies.append(KmuCompany(
                                                    name=name,
                                                    domain=domain,
                                                    sector=sector,
                                                    size=size,
                                                    website=website,
                                                    source="wko_wien",
                                                    notes=f"WKO member",
                                                    ))

                                                    return companies


                                                    def extract_hungrig_wien_founders(html: str | bytes) -> list[KmuCompany]:
                                                        """Extract Wien KMU from hungrig.tv Wien Unternehmensführer interviews.

                                                        Source: https://hungrig.tv/wien/unternehmensfuehrer/
                                                        Each interview features a Wien startup/SME founder and their company.
                                                        """
                                                        soup = BeautifulSoup(html, "lxml")

                                                        companies: list[KmuCompany] = []

                                                        # Pattern: article with founder interview
                                                        articles = soup.select("article, .interview, .unternehmer, .founder-interview")

                                                        for article in articles:
                                                            # Company name usually in h1, h2, or with "Unternehmen" marker
                                                            name_elem = article.select_one(
                                                            "h1, h2, h3, .company-name, .firma-name, .unternehmen-name, .firm"
                                                            )
                                                            if not name_elem:
                                                            continue

                                                            name = name_elem.get_text(strip=True)
                                                            if len(name) < 3:
                                                            continue

                                                            # Website link (often in footer or company section)
                                                            website_elem = article.select_one("a[href*='://'], .website, .link")
                                                            website = website_elem.get("href", "") if website_elem else None

                                                            # Extract domain
                                                            domain = ""
                                                            if website:
                                                            match = re.search(r"https?://([^/]+)", str(website))
                                                            if match:
                                                            domain = match.group(1).lower()
                                                            else:
                                                            domain = f"{name.lower().replace(' ', '-')}.at"

                                                            # Sector - often mentioned in article content
                                                            sector = "tech"  # hungrig.tv is focused on tech startups
                                                            desc_elem = article.select_one(".beschreibung, .branche, .description")
                                                            if desc_elem:
                                                            desc = desc_elem.get_text(strip=True).lower()
                                                            if any(kw in desc for kw in ["software", "tech", "digital", "saas", "startup"]):
                                                            sector = "technology"
                                                            elif "food" in desc or "gastronomie" in desc:
                                                            sector = "food"
                                                            elif "ecommerce" in desc or "handel" in desc:
                                                            sector = "ecommerce"

                                                            # Size - startups are typically small initially
                                                            size = "klein"
                                                            if "million" in article.get_text().lower() or "groß" in name.lower():
                                                            size = "mittel"

                                                            companies.append(KmuCompany(
                                                            name=name,
                                                            domain=domain,
                                                            sector=sector,
                                                            size=size,
                                                            website=website,
                                                            source="hungrig_wien",
                                                            notes="hungrig.tv founder interview",
                                                            ))

                                                            return companies


                                                            # ---------------------------------------------------------------------------
                                                            # Helper functions
                                                            # ---------------------------------------------------------------------------


                                                            def _parse_wirtschaftsagentur_json_list(items: list[dict[str, Any]]) -> list[KmuCompany]:
                                                                """Parse Wirtschaftsagentur JSON company list."""
                                                                companies: list[KmuCompany] = []
                                                                for item in items:
                                                                        if not isinstance(item, dict):
                                                                        continue

                                                                        name = item.get("name") or item.get("title") or item.get("companyName")
                                                                        if not name:
                                                                        continue

                                                                        # Extract website
                                                                        website = None
                                                                        if "url" in item and isinstance(item["url"], str):
                                                                        website = item["url"]
                                                                        elif "sameAs" in item and isinstance(item["sameAs"], str):
                                                                        website = item["sameAs"]

                                                                        # Extract domain
                                                                        domain = ""
                                                                        if website:
                                                                        match = re.search(r"https?://([^/]+)", str(website))
                                                                        if match:
                                                                        domain = match.group(1).lower()
                                                                        else:
                                                                        domain = f"{name.lower().replace(' ', '-')}.at"

                                                                        # Extract sector
                                                                        sector = "unknown"
                                                                        if "industry" in item:
                                                                        sector = str(item["industry"]).lower()
                                                                        elif "category" in item:
                                                                        sector = str(item["category"]).lower()
                                                                        elif "businessType" in item:
                                                                        sector = str(item["businessType"]).lower()

                                                                        companies.append(KmuCompany(
                                                                        name=name,
                                                                        domain=domain,
                                                                        sector=sector,
                                                                        website=website,
                                                                        source="wirtschaftsagentur_wien_json",
                                                                        notes=f"JSON: {name}",
                                                                        ))

                                                                        return companies


                                                                        # ---------------------------------------------------------------------------
                                                                        # Master discovery function
                                                                        # ---------------------------------------------------------------------------


                                                                        def discover_wien_kmu(html: str | bytes, source_type: str = "wirtschaftsagentur") -> list[KmuCompany]:
                                                                            """Discover Wien KMU companies from a source.

                                                                            Args:
                                                                                html: Pre-fetched content from the source page
                                                                                source_type: Which source to use ("wirtschaftsagentur", "firmenabc", "wko", "hungrig")

                                                                                Returns:
                                                                                    List of KmuCompany objects ready for conversion to SeedCompany
                                                                                    """
                                                                                    if source_type == "wirtschaftsagentur":
                                                                                    return extract_wirtschaftsagentur_wien(html)
                                                                                    elif source_type == "firmenabc":
                                                                                    return extract_firmenabc_wien(html)
                                                                                    elif source_type == "wko":
                                                                                    return extract_wko_wien(html)
                                                                                    elif source_type == "hungrig":
                                                                                    return extract_hungrig_wien_founders(html)
                                                                                    else:
                                                                                    raise ValueError(f"Unknown Wien KMU source: {source_type}")


                                                                                    def kmu_companies_to_seed_companies(companies: list[KmuCompany]) -> list[SeedCompany]:
                                                                                        """Convert discovered KMU companies to SeedCompany format.

                                                                                        Adds them to SEED_AUSTRIAN_COMPANIES as a new tier (Tier 3: career_path only).
                                                                                        No ATS tokens for these — they rely on the career page extractor.
                                                                                        """
                                                                                        seeds: list[SeedCompany] = []
                                                                                        for company in companies:
                                                                                            seeds.append(SeedCompany(
                                                                                            name=company.name,
                                                                                            domain=company.domain,
                                                                                            ats=None,  # No ATS for KMU discovery — career pages only
                                                                                            board_token=None,
                                                                                            sector=company.sector,
                                                                                            notes=f"KMU Wien {company.source}: {company.description}",
                                                                                            ))

                                                                                            return seeds


                                                                                            # ---------------------------------------------------------------------------
                                                                                            # Target URL builders for KMU companies
                                                                                            # ---------------------------------------------------------------------------


                                                                                            def build_kmu_career_urls(seed: SeedCompany) -> list[str]:
                                                                                                """Build candidate career URLs for a discovered KMU company.

                                                                                                Uses the standard CAREER_PATHS + CAREER_SUBDOMAINS from career_paths probe.
                                                                                                Returns URLs suitable for HEAD probing by the orchestrator.
                                                                                                """
                                                                                                from ..probes.career_paths import CAREER_PATHS, CAREER_SUBDOMAINS, _scheme
                                                                                                from urllib.parse import urljoin

                                                                                                domain = seed.domain.lower().strip()
                                                                                                if domain.startswith("www."):
                                                                                                domain = domain[4:]

                                                                                                base_scheme = _scheme(domain)
                                                                                                urls: list[str] = []

                                                                                                # Standard paths (German first for Austrian KMU)
                                                                                                for path in CAREER_PATHS:
                                                                                                    urls.append(f"{base_scheme}://{domain}{path}")

                                                                                                    # Career subdomains
                                                                                                    for sub in CAREER_SUBDOMAINS:
                                                                                                        urls.append(f"{base_scheme}://{sub}.{domain}/")

                                                                                                        return urls