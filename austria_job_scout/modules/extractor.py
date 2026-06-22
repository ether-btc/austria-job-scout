"""extractor — RawResponse → JobPosting (iter 3).

Iter-1 stub. Will dispatch by ATS fingerprint to the matching extractor in
`austria_job_scout/extractors/`.

ATS extractors to implement:
    ats_greenhouse.py    — boards-api.greenhouse.io JSON
    ats_lever.py         — api.lever.co JSON
    ats_smartrecruiters.py — api.smartrecruiters.com JSON
    ats_personio.py      — *.jobs.personio.de XML (?language=de|en)
    ats_successfactors.py — per-tenant XML (deferred v2)
    karriere_at.py       — HTML scrape
    jobs_at.py           — HTML scrape (likely needs JS render)
    generic_html.py      — BeautifulSoup fallback (last resort)
"""
from __future__ import annotations


def extract(*args, **kwargs):  # pragma: no cover - explicit stub
    raise NotImplementedError(
        "extractor is iter-3 work. "
        "See .planning/01-1-PLAN.md phase 4."
    )
