"""Permission policy for a *future* LLM / agent mode (dormant scaffold).

This module prepares the security posture described in
MadsLorentzen/ai-job-search's ``tools/security_guards.py`` *before* we
ever add an autonomous, tool-calling step (e.g. an LLM that drafts
applications or clicks through portals). It is intentionally **OFF** in
the current pipeline — importing it has no side effects and no network
access. It exists so that when agent mode lands, the blast radius is
already pinned to a tight allow-list rather than the open-ended
``Bash(*)`` that upstream templates ship by default.

Design mirrors the upstream convention:
  * ``ALLOWED_PERMISSIONS`` — the *only* tool/command categories the agent
    may invoke. Everything else is denied by default.
  * :class:`PermissionPolicy` — a callable gate: ``decide(tool, arg)``
    returns a :class:`PermissionDecision` (ALLOW / DENY / ASK). The policy
    can be enabled per-run via an env flag or constructor arg; disabled
    means "no agent activity at all" (the status quo).

Nothing here executes commands — it only *decides*. The executor (future)
calls ``policy.decide(...)`` and acts on the result.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List, Tuple


class PermissionDecision(str, Enum):
    """Outcome of a permission check."""

    ALLOW = "allow"   # safe, no human in the loop
    DENY = "deny"     # never permitted by this policy
    ASK = "ask"       # requires explicit human approval (future UI hook)


# The only tool/command families a future agent may touch. Kept deliberately
# minimal: read-only + the repo's own CLI entrypoints. Modeled on the six
# "shipped portal CLIs" tightening upstream applied in PR #396.
ALLOWED_PERMISSIONS: Tuple[str, ...] = (
    "Read",                       # read files / fetch URLs (read-only)
    "Glob",                       # list files by pattern
    "Grep",                       # search text (read-only)
    "Bash(git status)",           # inspect, never push/force
    "Bash(git diff)",             # inspect
    "Bash(python -m austria_job_scout",   # our own read-only subcommands
    "Bash(python -m austria_job_scout db-stats)",
    "Bash(python -m austria_job_scout discover",
    "Bash(python -m austria_job_scout discover-kmu",
)

# Patterns that are *always* denied, even if they share a prefix with an
# allowed command. Deny takes precedence over allow.
_DENY_ALWAYS: Tuple[str, ...] = (
    "Bash(git push",
    "Bash(git reset --hard",
    "Bash(rm -rf",
    "Bash(sudo",
    "Bash(curl",
    "Bash(wget",
    "Write",                      # no autonomous file writes yet
    "Edit",                       # no autonomous edits yet
    "WebFetch",                   # keep network egress off until reviewed
)


@dataclass
class PermissionPolicy:
    """Gate for future agent tool-use.

    Args:
        enabled: when False (default), *any* tool use is denied — the
            current no-agent behaviour. Flip to True only in an explicit
            agent run, ideally gated behind an env var.
        extra_allow: additional permission strings to permit this run.
    """

    enabled: bool = False
    extra_allow: Iterable[str] = ()

    def __post_init__(self) -> None:
        self._allow = set(ALLOWED_PERMISSIONS) | set(self.extra_allow or ())

    @classmethod
    def from_env(cls, env: "os._Environ[str] | None" = None) -> "PermissionPolicy":
        """Build from the environment.

        Agent mode turns on only when ``AJS_AGENT_MODE=1`` is set — never
        by default. This matches the "deny by default" upstream posture.
        """
        env = env if env is not None else os.environ
        enabled = env.get("AJS_AGENT_MODE", "") == "1"
        return cls(enabled=enabled)

    def decide(self, tool: str, arg: str = "") -> PermissionDecision:
        """Decide whether ``tool`` with ``arg`` may run.

        Returns DENY when the policy is disabled (no agent) or the
        tool/arg matches a deny rule; ASK for anything outside the allow
        list while enabled (so new tools prompt rather than silently run);
        ALLOW only for explicit allow-list matches.
        """
        if not self.enabled:
            return PermissionDecision.DENY

        permission = f"{tool}({arg})" if arg else tool

        # Deny rules win first.
        for d in _DENY_ALWAYS:
            if permission.startswith(d) or permission == d:
                return PermissionDecision.DENY

        # Allow only exact / prefix matches from the curated set.
        for a in self._allow:
            if permission == a or permission.startswith(a):
                return PermissionDecision.ALLOW

        # Unknown tool while agent mode is on → ask a human, never auto-run.
        return PermissionDecision.ASK

    def audit(self, calls: List[Tuple[str, str]]) -> List[Tuple[str, str, PermissionDecision]]:
        """Replay a list of (tool, arg) calls through :meth:`decide`.

        Useful for tests and for reviewing an agent trace after the fact.
        """
        return [(t, a, self.decide(t, a)) for (t, a) in calls]
