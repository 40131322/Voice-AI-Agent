"""Clinic triage task package (the Medical Agent's brain).

``triage_screen`` is a TASK (it calls OpenRouter/Anthropic directly), not a
Composio tool — mirrors ``tasks/search_email/``. Register it in
``server/agents/execution_agent/tasks/__init__.py`` alongside search_email.
"""

from .schemas import get_schemas
from .tool import build_registry

__all__ = ["get_schemas", "build_registry"]
