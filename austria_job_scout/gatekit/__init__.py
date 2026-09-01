"""Pre-index gate kit + future LLM permission policy.

Two independent concerns live here so the repo is ready for both:

1. :mod:`austria_job_scout.gatekit.gates` — deterministic, no-LLM
   eligibility / language *pre-filters* that run **before** jobs are
   fetched/indexed/scored. These are the scout-side counterpart of the
   post-score gates already in cv-profile-assessment (ported from
   MadsLorentzen/ai-job-search's 04-job-evaluation.md).

2. :mod:`austria_job_scout.gatekit.permissions` — a *dormant*
   :class:`PermissionPolicy` that will constrain any future LLM/agent
   tool-use (e.g. an autonomous apply step). It mirrors the upstream
   ai-job-search ``tools/security_guards.py`` convention: a tight
   allow-list of commands + an approval gate, OFF by default so the
   current Pi-local, fully-reproducible pipeline is untouched.

Nothing here performs network or filesystem side effects on import.
"""

from .gates import (
    GateResult,
    Verdict,
    check_eligibility_gate,
    check_language_gate,
    gate_targets,
    run_gates,
)
from .permissions import (
    ALLOWED_PERMISSIONS,
    PermissionDecision,
    PermissionPolicy,
)

__all__ = [
    "GateResult",
    "Verdict",
    "check_eligibility_gate",
    "check_language_gate",
    "gate_targets",
    "run_gates",
    "ALLOWED_PERMISSIONS",
    "PermissionDecision",
    "PermissionPolicy",
]
