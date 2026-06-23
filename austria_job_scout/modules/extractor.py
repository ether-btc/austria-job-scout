"""extractor — dispatch raw responses to the right extractor.

Routes a fetched RawResponse to the matching ATS or aggregator extractor
based on its `ats_fingerprint`. This is the Pillar 0-compliant entry point:
it accepts pre-fetched HTML text and never makes additional network requests.
"""
from __future__ import annotations

from typing import Any, Union

from austria_job_scout.extractors.ats_extractor import (
    ATSJob,
    extract_from_html as _extract_ats,
)
from austria_job_scout.extractors.aggregator_extractor import (
    AggregatorJob,
    extract_from_html as _extract_aggregator,
)
from austria_job_scout.modules.fetcher import RawResponse

_ATS_FINGERPRINTS = frozenset({
    "workday", "greenhouse", "lever", "smartrecruiters",
    "personio", "successfactors", "workable", "recruitee",
})

ExtractedJob = Union[ATSJob, AggregatorJob]


def extract(resp: RawResponse) -> list[ExtractedJob]:
    """Extract structured job data from a pre-fetched RawResponse.

    Dispatches by ats_fingerprint:
    - ATS sites → ats_extractor.extract_from_html
    - Everything else → aggregator_extractor.extract_from_html

    Returns an empty list if the response has no usable body.
    """
    if resp.text is None or resp.status_code is None or resp.status_code >= 400:
        return []

    fp = (resp.ats_fingerprint or "").lower()

    if fp in _ATS_FINGERPRINTS or any(a in fp for a in _ATS_FINGERPRINTS):
        job = _extract_ats(resp.url, resp.text)
        return [job] if job else []

    jobs = _extract_aggregator(resp.url, resp.text)
    return jobs or []
