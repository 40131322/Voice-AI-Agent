"""Resolve an execution agent's role from its name.

Two dedicated, tool-scoped agents exist:

- ``gmail-agent``    → Gmail sending/search tools only
- ``calendar-agent`` → Google Calendar tools only

Every other agent name resolves to the ``general`` role and keeps the original
full toolset (Gmail + email search + triggers), preserving backward
compatibility with dynamically-named agents. All roles additionally receive the
inter-agent ``message_agent`` tool so the specialized agents can collaborate.
"""

from __future__ import annotations

GMAIL_AGENT = "gmail-agent"
CALENDAR_AGENT = "calendar-agent"

ROLE_GMAIL = "gmail"
ROLE_CALENDAR = "calendar"
ROLE_GENERAL = "general"

_ROLE_BY_NAME = {
    GMAIL_AGENT: ROLE_GMAIL,
    CALENDAR_AGENT: ROLE_CALENDAR,
}


def resolve_role(agent_name: str | None) -> str:
    """Map an agent name to its role. Unknown names are ``general``."""
    return _ROLE_BY_NAME.get((agent_name or "").strip().lower(), ROLE_GENERAL)


__all__ = [
    "GMAIL_AGENT",
    "CALENDAR_AGENT",
    "ROLE_GMAIL",
    "ROLE_CALENDAR",
    "ROLE_GENERAL",
    "resolve_role",
]
