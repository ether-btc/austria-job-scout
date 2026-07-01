"""German-English skill/role synonym dictionary for cross-language matching.

Maps German job terms to their English equivalents so that the TF-IDF
similarity scorer can match a German job title (e.g. "Vertriebsprofi")
against an English reference (e.g. "Sales Professional").

Usage:
    from austria_job_scout.modules.de_en_synonyms import normalize_to_english
    english_text = normalize_to_english(german_text)

The dictionary is deliberately conservative — only terms that appear
frequently in Austrian job postings and have unambiguous English
equivalents are included.
"""

# (German term, English equivalent)
# Both directions are indexed for lookup.
_SYNONYMS: dict[str, str] = {
    # --- Roles / Job titles ---
    "vertrieb": "sales",
    "vertriebsprofi": "sales professional",
    "vertriebsmitarbeiter": "sales representative",
    "verkaufsleiter": "head of sales",
    "verkäufer": "salesperson",
    "verkauf": "sales",
    "außendienst": "field sales",
    "innendienst": "inside sales",
    "kundenbetreuer": "account manager",
    "kundenbetreuung": "customer service",
    "kundenberater": "customer advisor",
    "kundenberatung": "customer consulting",
    "kundenservice": "customer service",
    "kundenmanagement": "account management",
    "projektmanager": "project manager",
    "projektmanagement": "project management",
    "marketingmanager": "marketing manager",
    "marketing": "marketing",
    "online marketing": "online marketing",
    "social media manager": "social media manager",
    "content manager": "content manager",
    "recruiter": "recruiter",
    "personalbetreuer": "hr manager",
    "personalwesen": "human resources",
    "personalsachbearbeiter": "hr administrator",
    "buchhalter": "accountant",
    "buchhaltung": "accounting",
    "finanzbuchhaltung": "financial accounting",
    "controller": "controller",
    "controlling": "controlling",
    "assistent": "assistant",
    "assistenz": "assistant",
    "sekretär": "secretary",
    "sekretariat": "secretary",
    "büroleiter": "office manager",
    "empfang": "reception",
    "it-support": "it support",
    "it-administrator": "it administrator",
    "systemadministrator": "system administrator",
    "helpdesk": "helpdesk",
    "first-level-support": "1st level support",
    "erstanwenderbetreuung": "first level support",

    # --- Skills / Competencies ---
    "kommunikationsfähigkeit": "communication skills",
    "kollaboration": "collaboration",
    "einfühlungsvermögen": "empathy",
    "kundenorientierung": "customer orientation",
    "verhandlungsgeschick": "negotiation skills",
    "verhandlungsführung": "negotiation",
    "lösungsorientierung": "problem-solving",
    "teamfähigkeit": "teamwork",
    "selbstständige arbeitsweise": "independent working",
    "strukturierte arbeitsweise": "structured working",
    "durchsetzungsvermögen": "assertiveness",
    "interkulturelle kompetenz": "intercultural competence",

    # --- Business terms ---
    "akquise": "acquisition",
    "neukundengewinnung": "new customer acquisition",
    "bestandskundenbetreuung": "existing customer management",
    "kaltakquise": "cold calling",
    "vertriebssteuerung": "sales management",
    "vertriebsstrategie": "sales strategy",
    "lead-generierung": "lead generation",
    "lead-qualifizierung": "lead qualification",
    "angebotserstellung": "proposal preparation",
    "angebotskalkulation": "quote calculation",
    "verkaufsabschluss": "sales closing",
    "umsatzziel": "revenue target",
    "vertriebsziel": "sales target",
    "umsatzsteigerung": "revenue growth",
    "kundenbindung": "customer retention",
    "kundenrückgewinnung": "customer win-back",
    "beschwerdemanagement": "complaint management",
    "reklamationsmanagement": "claims management",
    "auftragsabwicklung": "order processing",
    "auftragsmanagement": "order management",
    "projektsteuerung": "project management",
    "projektcontrolling": "project controlling",
    "ressourcenplanung": "resource planning",
    "prozessoptimierung": "process optimization",
    "geschäftsprozess": "business process",
    " veranderungsmanagement": "change management",

    # --- Tools / Technologies ---
    "kundenmanagement-system": "crm",
    "kundendatenbank": "customer database",
    "vertriebscontrolling": "sales controlling",
    "betriebsrat": "works council",
    "onboarding": "onboarding",
    "offboarding": "offboarding",
    "personalbeschaffung": "recruitment",
    "personalauswahl": "candidate selection",
    "cv-screening": "cv screening",
    "bewerbermanagement": "applicant management",
    "interviewführung": "interviewing",
    "kandidatenbetreuung": "candidate management",
    "stellenanzeigen": "job advertisements",
    "stellenbeschreibung": "job description",
    "arbeitszeugnis": "employment reference",
    "gehalt verhandlung": "salary negotiation",

    # --- Employment terms ---
    "vollzeit": "full-time",
    "teilzeit": "part-time",
    "befristet": "temporary",
    "unbefristet": "permanent",
    "festanstellung": "permanent employment",
    "freelance": "freelance",
    "werkstudent": "working student",
    "praktikant": "intern",
    "azubi": "apprentice",
    "homeoffice": "remote work",
    "remote": "remote",
    "hybrid": "hybrid",
    "gleitzeit": "flexible hours",
    "überstunden": "overtime",

    # --- Industries ---
    "versicherung": "insurance",
    "bankwesen": "banking",
    "finanzwesen": "finance",
    "immobilien": "real estate",
    "einzelhandel": "retail",
    "großhandel": "wholesale",
    "b2b": "b2b",
    "b2c": "b2c",
    "telekommunikation": "telecommunications",
    "it-branche": "it industry",
    "softwareentwicklung": "software development",

    # --- Languages ---
    "deutsch": "german",
    "englisch": "english",
    "ungarisch": "hungarian",
    "muttersprache": "native speaker",
    "verhandlungssicher": "fluent",
    "fließend": "fluent",
    "grundkenntnisse": "basic knowledge",
    "business english": "business english",
}


def normalize_to_english(text: str) -> str:
    """Replace German terms with English equivalents in text.

    Non-destructive: terms not in the dictionary pass through unchanged.
    Case-insensitive matching, preserves original casing for non-matched text.
    """
    if not text:
        return text
    result = text
    for de_term, en_term in _SYNONYMS.items():
        # Case-insensitive replacement
        result = result.lower().replace(de_term, en_term)
    return result


def get_synonyms(term: str) -> list[str]:
    """Return all known synonyms for a term (both directions)."""
    term_lower = term.lower().strip()
    result = []
    # Forward: German → English
    if term_lower in _SYNONYMS:
        result.append(_SYNONYMS[term_lower])
    # Reverse: English → German
    for de, en in _SYNONYMS.items():
        if en == term_lower:
            result.append(de)
    return result


SYNONYM_COUNT = len(_SYNONYMS)
