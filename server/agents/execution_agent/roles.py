"""Resolve an execution agent's role from its name.

Three dedicated, tool-scoped agents exist:

- ``gmail-agent``    → Gmail sending/search tools only
- ``calendar-agent`` → Google Calendar tools only
- ``medical-agent``  → clinical triage screening only (``triage_screen``)

Every other agent name resolves to the ``general`` role and keeps the original
full toolset (Gmail + email search + triggers), preserving backward
compatibility with dynamically-named agents.

Only the ``general`` role receives the inter-agent ``message_agent`` tool. The
three specialized roles are deliberately pure leaf workers: the interaction
agent orchestrates cross-agent steps (e.g. book, then email a confirmation).
Handing a leaf agent ``message_agent`` previously let the calendar agent
delegate to the gmail agent after booking, triggering a mailbox search that blew
the model context and timed the batch out — reporting a false failure to a
booking that had actually succeeded.
"""

from __future__ import annotations

GMAIL_AGENT = "gmail-agent"
CALENDAR_AGENT = "calendar-agent"
MEDICAL_AGENT = "medical-agent"

ROLE_GMAIL = "gmail"
ROLE_CALENDAR = "calendar"
ROLE_MEDICAL = "medical"
ROLE_GENERAL = "general"

_ROLE_BY_NAME = {
    GMAIL_AGENT: ROLE_GMAIL,
    CALENDAR_AGENT: ROLE_CALENDAR,
    MEDICAL_AGENT: ROLE_MEDICAL,
}

# Substring fallback so a renamed/mis-cased specialized agent (e.g. "Calendar
# Agent", "clinic-scheduler") still gets its scoped toolset instead of silently
# resolving to ``general`` and being handed the full Gmail + email-search +
# message_agent surface — the leak that let the calendar agent run away.
# Ordered by specificity: check triage/medical before the broader terms.
_ROLE_BY_KEYWORD = (
    ("triage", ROLE_MEDICAL),
    ("medical", ROLE_MEDICAL),
    ("clinic", ROLE_CALENDAR),
    ("schedul", ROLE_CALENDAR),
    ("calendar", ROLE_CALENDAR),
    ("gmail", ROLE_GMAIL),
    ("email", ROLE_GMAIL),
)


def resolve_role(agent_name: str | None) -> str:
    """Map an agent name to its role. Unknown names are ``general``.

    Exact names win first; otherwise a keyword in the name scopes the agent.
    Only names with no recognizable role fall through to ``general``.
    """
    name = (agent_name or "").strip().lower()
    if name in _ROLE_BY_NAME:
        return _ROLE_BY_NAME[name]
    for keyword, role in _ROLE_BY_KEYWORD:
        if keyword in name:
            return role
    return ROLE_GENERAL


__all__ = [
    "GMAIL_AGENT",
    "CALENDAR_AGENT",
    "MEDICAL_AGENT",
    "ROLE_GMAIL",
    "ROLE_CALENDAR",
    "ROLE_MEDICAL",
    "ROLE_GENERAL",
    "resolve_role",
]
