"""Aggregate execution agent tool schemas and registries, scoped by agent role.

- ``gmail`` role    → Gmail tools + email search
- ``calendar`` role → Google Calendar tools
- ``general`` role  → Gmail + email search + triggers (original behavior)

Every role additionally gets the inter-agent ``message_agent`` tool so the
specialized agents can collaborate.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from . import agent_comm, calendar, gmail, triggers
from ..roles import ROLE_CALENDAR, ROLE_GMAIL, resolve_role
from ..tasks import get_task_registry, get_task_schemas


# Return OpenAI/OpenRouter-compatible tool schemas for the given agent's role
def get_tool_schemas(agent_name: str | None = None) -> List[Dict[str, Any]]:
    """Return tool schemas scoped to the agent's role."""

    role = resolve_role(agent_name)

    if role == ROLE_GMAIL:
        schemas = [*gmail.get_schemas(), *get_task_schemas()]
    elif role == ROLE_CALENDAR:
        schemas = [*calendar.get_schemas()]
    else:  # general (backward-compatible full toolset)
        schemas = [*gmail.get_schemas(), *get_task_schemas(), *triggers.get_schemas()]

    schemas.extend(agent_comm.get_schemas())
    return schemas


# Return Python callables for executing tools by name, scoped to the agent's role
def get_tool_registry(agent_name: str) -> Dict[str, Callable[..., Any]]:
    """Return tool callables scoped to the agent's role."""

    role = resolve_role(agent_name)
    registry: Dict[str, Callable[..., Any]] = {}

    if role == ROLE_GMAIL:
        registry.update(gmail.build_registry(agent_name))
        registry.update(get_task_registry(agent_name))
    elif role == ROLE_CALENDAR:
        registry.update(calendar.build_registry(agent_name))
    else:  # general (backward-compatible full toolset)
        registry.update(gmail.build_registry(agent_name))
        registry.update(get_task_registry(agent_name))
        registry.update(triggers.build_registry(agent_name))

    registry.update(agent_comm.build_registry(agent_name))
    return registry


__all__ = [
    "get_tool_registry",
    "get_tool_schemas",
]
