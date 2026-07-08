"""Clinic triage task implementation (mirror of tasks/search_email/tool.py).

Reads the session file, asks the model for a triage verdict, writes the result
back to session['triage'], and flips session['status'] to "emergency" on a 911
finding so the calendar booking guard (tools/calendar.py) can refuse.

This is a TASK: it calls the model directly via server.openrouter_client, exactly
like the email-search task — it is NOT a Composio tool.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict

from server.config import get_settings
from server.logging_config import logger
from server.openrouter_client import request_chat_completion
from server.services.execution import get_execution_agent_logs
from server.services.session import patch_session, read_session

from .schemas import TASK_TOOL_NAME, TriageResult, TriageToolResult
from .system_prompt import get_system_prompt

_LOG_STORE = get_execution_agent_logs()
_TRIAGE_AGENT_NAME = "medical-execution-agent"


def _model_id() -> str:
    """Cheaper model on the hot path.

    TODO: add `triage_model` to server/config.py Settings and return it here.
    Falls back to the execution model if not present.
    """
    settings = get_settings()
    return getattr(settings, "triage_model", None) or settings.execution_agent_model


def triage_screen(call_id: str) -> Dict[str, Any]:
    """Screen the current intake for an emergency and update the session file."""
    session = read_session(call_id)
    intake = session.get("intake", {})
    caller = session.get("caller", {})

    if not get_settings().openrouter_api_key:
        return TriageToolResult(
            status="error", call_id=call_id,
            error="OpenRouter API key not configured. Set OPENROUTER_API_KEY.",
        ).model_dump(exclude_none=True)

    user_payload = json.dumps(
        {"caller": caller, "intake": intake}, ensure_ascii=False
    )

    messages = [
        {"role": "system", "content": get_system_prompt()},
        {
            "role": "user",
            "content": (
                "Screen this intake and return a triage verdict as JSON with keys "
                "level, urgency, confidence, rationale, red_flags.\n\n" + user_payload
            ),
        },
    ]

    try:
        # TODO: wire this to the real request_chat_completion signature used by
        # tasks/search_email/tool.py (model, messages, and — ideally — a
        # tool/function schema so the model returns TriageResult directly).
        # For the MVP a JSON-mode text completion parsed below is fine.
        response = request_chat_completion(model=_model_id(), messages=messages)
        content = _extract_content(response)
        verdict = TriageResult(**json.loads(content))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[triage] screen failed for %s: %s", call_id, exc)
        _LOG_STORE.record_action(
            _TRIAGE_AGENT_NAME, description=f"triage_screen failed | {exc}"
        )
        return TriageToolResult(
            status="error", call_id=call_id, error=str(exc)
        ).model_dump(exclude_none=True)

    # Write verdict back to the blackboard.
    patch: Dict[str, Any] = {"triage": verdict.model_dump()}
    if verdict.level == "emergency":
        patch["status"] = "emergency"  # booking guard reads this
    patch_session(call_id, patch)

    _LOG_STORE.record_action(
        _TRIAGE_AGENT_NAME,
        description=f"triage_screen -> {verdict.level} (urgency {verdict.urgency})",
    )
    return TriageToolResult(
        status="success", call_id=call_id, triage=verdict
    ).model_dump(exclude_none=True)


def _extract_content(response: Any) -> str:
    """Pull the assistant text out of an OpenRouter chat response.

    TODO: match the exact response shape returned by
    server.openrouter_client.request_chat_completion (see search_email/tool.py).
    """
    if isinstance(response, dict):
        return response["choices"][0]["message"]["content"]
    return str(response)


def build_registry(agent_name: str) -> Dict[str, Callable[..., Any]]:  # noqa: ARG001
    """Return the triage task callables keyed by tool name."""
    return {TASK_TOOL_NAME: triage_screen}


__all__ = ["triage_screen", "build_registry"]
