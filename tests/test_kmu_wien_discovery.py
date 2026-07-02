"""Tests for KMU Wien discovery module."""
from __future__ import annotations

import pytest

from austria_job_scout.modules.kmu_wien_discovery import (
    KmuCompany,
    discover_wien_kmu,
    kmu_companies_to_seed_companies,
)


# ---------------------------------------------------------------------------
# Test fixtures - mock HTML content
# ---------------------------------------------------------------------------

WIRTSCHAFTSAGENTUR_HTML = """
<!DOCTYPE html>
<html>
<head>
    <script type="application/ld+json">
    [
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": "Tech Startup GmbH",
            "url": "https://techstartup.at"
        },
        {
            "@context": "https://schema.org", 
            "@type": "Organization",
            "name": "Digital Solutions AG",
            "url": "https://digitalsolutions.at",
            "industry": "Information Technology"
        }
    ]
    </script>
</head>
<body>
    <div class="unternehmen-item">
        <h3>Kleinunternehmensname GmbH</h3>
        <a href="https://kleinunternehmen.at" class="website-link">Website</a>
        <p class="sektor">Dienstleistungen</p>
    </div>
</body>
</html>
"""

FIRMENABC_HTML = """
<!DOCTYPE html>
<html>
<body>
    <div class="firma-entry">
        <h4>Web Design Wien GmbH</h4>
        <a href="https://webdesignwien.at">Visit website</a>
        <p class="beschreibung">Web development services</p>
    </div>
    <div class="firma-entry">
        <h4>Marketing Agentur</h4>
        <a href="https://marketing.at">Marketing services</a>
        <p class="branche">Marketing</p>
    </div>
</body>
</html>
"""

WKO_HTML = """
<!DOCTYPE html>
<html>
<body>
    <table class="firmenliste">
        <tr>
            <td class="firmenname">Handels GmbH</td>
            <td class="branche">Handel</td>
        </tr>
        <tr>
            <td class="firmenname">Industrie AG</td>
            <td class="branche">Industrie</td>
        </tr>
    </table>
    <div class="wko-member">
        <h4>Finanzdienstleistungen KG</h4>
        <p class="category">Dienstleistungen</p>
    </div>
</body>
</html>
"""

HUNGRIG_HTML = """
<!DOCTYPE html>
<html>
<body>
    <article class="interview">
        <h1>Gründerinterview: StartupX</h1>
        <div class="company-name">StartupX GmbH</div>
        <p class="beschreibung">Technologie Startup in Wien</p>
    </article>
    <article class="founder-interview">
        <h2>Unternehmensführer: FoodTech Wien</h2>
        <a href="https://foodtech.at">Website</a>
        <p class="description">Food delivery startup</p>
    </article>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_wirtschaftsagentur_json_extraction():
    """Test extraction from Wirtschaftsagentur JSON-LD."""
    companies = discover_wien_kmu(WIRTSCHAFTSAGENTUR_HTML, "wirtschaftsagentur")
    
    assert len(companies) >= 2
    
    # Check JSON-LD extracted companies
    json_companies = [c for c in companies if c.source == "wirtschaftsagentur_wien_json"]
    assert len(json_companies) >= 2
    
    tech_startup = next((c for c in json_companies if c.name == "Tech Startup GmbH"), None)
    assert tech_startup is not None
    assert tech_startup.domain == "techstartup.at"
    assert tech_startup.sector == "unknown"  # JSON has no industry field
    
    digital_solutions = next((c for c in json_companies if c.name == "Digital Solutions AG"), None)
    assert digital_solutions is not None
    assert digital_solutions.domain == "digitalsolutions.at"
    assert digital_solutions.sector == "information technology"


def test_wirtschaftsagentur_html_fallback():
    """Test HTML parsing fallback for Wirtschaftsagentur."""
    companies = discover_wien_kmu(WIRTSCHAFTSAGENTUR_HTML, "wirtschaftsagentur")
    
    # Should find the HTML item too
    html_companies = [c for c in companies if c.source == "wirtschaftsagentur_wien"]
    assert len(html_companies) >= 1
    
    kleinunternehmen = next((c for c in html_companies if c.name == "Kleinunternehmensname GmbH"), None)
    assert kleinunternehmen is not None
    assert kleinunternehmen.domain == "kleinunternehmen.at"
    assert kleinunternehmen.sector == "dienstleistungen"
    assert kleinunternehmen.size == "klein"


def test_firmenabc_extraction():
    """Test extraction from firmenabc.at directory."""
    companies = discover_wien_kmu(FIRMENABC_HTML, "firmenabc")
    
    assert len(companies) == 2
    
    web_design = next((c for c in companies if c.name == "Web Design Wien GmbH"), None)
    assert web_design is not None
    assert web_design.domain == "webdesignwien.at"
    assert web_design.sector == "web development services"
    assert web_design.website == "https://webdesignwien.at"
    
    marketing = next((c for c in companies if c.name == "Marketing Agentur"), None)
    assert marketing is not None
    assert marketing.domain == "marketing.at"
    assert marketing.sector == "marketing"


def test_wko_extraction():
    """Test extraction from WKO listings."""
    companies = discover_wien_kmu(WKO_HTML, "wko")
    
    assert len(companies) >= 2
    
    # Check WKO member company
    wko_companies = [c for c in companies if c.source == "wko_wien"]
    assert len(wko_companies) >= 1
    
    finanz = next((c for c in companies if c.name == "Finanzdienstleistungen KG"), None)
    assert finanz is not None
    assert finanz.domain == "finanzdienstleistungen.at"
    assert finanz.sector == "dienstleistungen"
    assert finanz.size == "klein"  # Default for KG


def test_hungrig_extraction():
    """Test extraction from hungrig Wien founder interviews."""
    companies = discover_wien_kmu(HUNGRIG_HTML, "hungrig")
    
    assert len(companies) >= 2
    
    startupx = next((c for c in companies if c.name == "StartupX GmbH"), None)
    assert startupx is not None
    assert startupx.domain == "startupx.at"
    assert startupx.sector == "tech"
    assert startupx.size == "klein"
    
    foodtech = next((c for c in companies if c.name == "FoodTech Wien"), None)
    assert foodtech is not None
    assert foodtech.domain == "foodtech.at"
    assert foodtech.website == "https://foodtech.at"
    assert foodtech.sector == "food"


def test_kmu_companies_to_seed_companies():
    """Test conversion of KmuCompany to SeedCompany."""
    kmu_companies = [
        KmuCompany(
            name="Test GmbH",
            domain="test.at",
            sector="technology",
            size="klein",
            source="test"
        ),
        KmuCompany(
            name="Big Corp AG",
            domain="bigcorp.at", 
            sector="finance",
            size="gross",
            source="test"
        )
    ]
    
    seeds = kmu_companies_to_seed_companies(kmu_companies)
    
    assert len(seeds) == 2
    
    test_seed = next((s for s in seeds if s.name == "Test GmbH"), None)
    assert test_seed is not None
    assert test_seed.domain == "test.at"
    assert test_seed.ats is None  # No ATS for KMU discovery
    assert test_seed.sector == "technology"
    assert "Test GmbH" in test_seed.notes
    
    big_seed = next((s for s in seeds if s.name == "Big Corp AG"), None)
    assert big_seed is not None
    assert big_seed.domain == "bigcorp.at"
    assert big_seed.ats is None
    assert big_seed.sector == "finance"


def test_unknown_source():
    """Test error handling for unknown source type."""
    with pytest.raises(ValueError, match="Unknown Wien KMU source"):
        discover_wien_kmu(WIRTSCHAFTSAGENTUR_HTML, "unknown_source")


def test_empty_html():
    """Test extraction with empty HTML content."""
    companies = discover_wien_kmu("", "wirtschaftsagentur")
    assert companies == []


def test_size_classification():
    """Test automatic size classification logic."""
    # Test tech startup gets medium size
    tech_html = """
    <html><body>
        <div class="unternehmen-item">
            <h3>Software Solutions GmbH</h3>
            <p class="sektor">Tech</p>
            <a href="https://software.at"></a>
        </div>
    </body></html>
    """
    companies = discover_wien_kmu(tech_html, "wirtschaftsagentur")
    tech_company = companies[0]
    assert tech_company.size == "mittel"  # Tech defaults to medium
    
    # Test AG/GmbH gets medium size
    ag_html = """
    <html><body>
        <div class="unternehmen-item">
            <h3>AG Corporation</h3>
            <a href="https://ag.at"></a>
        </div>
    </body></html>
    """
    companies = discover_wien_kmu(ag_html, "wirtschaftsagentur")
    ag_company = companies[0]
    assert ag_company.size == "mittel"


def test_sector_classification():
    """Test sector classification from various sources."""
    # Test sector extraction from description
    sector_html = """
    <html><body>
        <div class="unternehmen-item">
            <h3>Bau GmbH</h3>
            <p class="sektor">Bau</p>
        </div>
    </body></html>
    """
    companies = discover_wien_kmu(sector_html, "wirtschaftsagentur")
    bau_company = companies[0]
    assert bau_company.sector == "bau"
    
    # Test default sector
    default_html = """
    <html><body>
        <div class="unternehmen-item">
            <h3>No Sector GmbH</h3>
        </div>
    </body></html>
    """
    companies = discover_wien_kmu(default_html, "wirtschaftsagentur")
    default_company = companies[0]
    assert default_company.sector == "unknown"


def test_build_kmu_career_urls():
    """Test career URL building for KMU companies."""
    from austria_job_scout.modules.kmu_wien_discovery import build_kmu_career_urls
    
    # Create a test seed company
    from austria_job_scout.seeds import SeedCompany
    seed = SeedCompany(
        name="Test GmbH",
        domain="test.at",
        ats=None,
        board_token=None,
        sector="technology"
    )
    
    urls = build_kmu_career_urls(seed)
    
    # Should have multiple career paths
    assert len(urls) > 5
    
    # Check for common patterns
    karriere_urls = [u for u in urls if "/karriere" in u]
    assert len(karriere_urls) > 0
    
    # Check for subdomains
    subdomain_urls = [u for u in urls if "jobs." in u or "careers." in u]
    assert len(subdomain_urls) > 0
    
    # Check all URLs use https
    for url in urls:
        assert url.startswith("https://")


def test_kmu_company_dataclass():
    """Test KmuCompany dataclass functionality."""
    company = KmuCompany(
        name="Test Company",
        domain="test.at",
        sector="technology",
        size="klein",
        location="Wien",
        website="https://test.at",
        description="A test company",
        source="test_source",
        notes="Test notes"
    )
    
    assert company.name == "Test Company"
    assert company.domain == "test.at"
    assert company.sector == "technology"
    assert company.size == "klein"
    assert company.location == "Wien"
    assert company.website == "https://test.at"
    assert company.description == "A test company"
    assert company.source == "test_source"
    assert company.notes == "Test notes"