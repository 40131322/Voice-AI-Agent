"""Clinic triage task implementation (mirror of tasks/search_email/tool.py).

Reads the session file, asks the model for a triage verdict, writes the result
back to session['triage'], and flips session['status'] to "emergency" on a 911
finding so the calendar booking guard (tools/calendar.py) can refuse.

This is a TASK: it calls the model directly via server.openrouter_client, exactly
like the email-search task — it is NOT a Composio tool. It is async because
``request_chat_completion`` is async (see search_email/tool.py).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from server.config import get_settings
from server.logging_config import logger
from server.openrouter_client import request_chat_completion
from server.services.execution import get_execution_agent_logs
from server.services.session import patch_session, read_session

from .schemas import TASK_TOOL_NAME, TriageResult, TriageToolResult
from .system_prompt import get_system_prompt

_LOG_STORE = get_execution_agent_logs()
_TRIAGE_AGENT_NAME = "medical-execution-agent"
_SUBMIT_TOOL_NAME = "submit_triage"

# Completion tool the triage model calls to return a structured verdict. Passing
# it as a tool (rather than trusting free-text JSON) makes the shape reliable; we
# still fall back to parsing message content if the model answers in text.
_TRIAGE_COMPLETION_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": _SUBMIT_TOOL_NAME,
        "description": "Return the triage verdict for the caller's intake.",
        "parameters": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "enum": ["emergency", "urgent", "routine"],
                },
                "urgency": {"type": "integer", "minimum": 1, "maximum": 5},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "rationale": {"type": "string"},
                "red_flags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["level", "urgency", "confidence", "rationale"],
            "additionalProperties": False,
        },
    },
}


def _model_id() -> str:
    """Cheaper model on the hot path (OPENPOKE_TRIAGE_MODEL, see config.py)."""
    settings = get_settings()
    return getattr(settings, "triage_model", None) or settings.execution_agent_model


async def triage_screen(call_id: str) -> Dict[str, Any]:
    """Screen the current intake for an emergency and update the session file."""
    session = read_session(call_id)
    intake = session.get("intake", {})
    caller = session.get("caller", {})

    settings = get_settings()
    if not settings.openrouter_api_key:
        return TriageToolResult(
            status="error",
            call_id=call_id,
            error="OpenRouter API key not configured. Set OPENROUTER_API_KEY.",
        ).model_dump(exclude_none=True)

    user_payload = json.dumps({"caller": caller, "intake": intake}, ensure_ascii=False)
    messages = [
        {
            "role": "user",
            "content": (
                "Screen this intake and return a triage verdict by calling "
                f"{_SUBMIT_TOOL_NAME} with level, urgency, confidence, rationale, "
                "red_flags.\n\n" + user_payload
            ),
        }
    ]

    try:
        response = await request_chat_completion(
            model=_model_id(),
            messages=messages,
            system=get_system_prompt(),
            api_key=settings.openrouter_api_key,
            tools=[_TRIAGE_COMPLETION_SCHEMA],
        )
        verdict = _parse_verdict(response)
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
        patch["status"] = "emergency"  # booking guard (tools/calendar.py) reads this
    patch_session(call_id, patch)

    _LOG_STORE.record_action(
        _TRIAGE_AGENT_NAME,
        description=f"triage_screen -> {verdict.level} (urgency {verdict.urgency})",
    )
    return TriageToolResult(
        status="success", call_id=call_id, triage=verdict
    ).model_dump(exclude_none=True)


def _parse_verdict(response: Dict[str, Any]) -> TriageResult:
    """Extract a TriageResult from an OpenRouter response.

    Prefers the structured ``submit_triage`` tool call; falls back to parsing the
    assistant's text content as JSON (tolerating ```json fences).
    """
    message = (response.get("choices") or [{}])[0].get("message", {}) or {}

    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        if function.get("name") == _SUBMIT_TOOL_NAME:
            raw = function.get("arguments") or "{}"
            args = raw if isinstance(raw, dict) else json.loads(raw)
            return TriageResult(**args)

    content = (message.get("content") or "").strip()
    if content:
        return TriageResult(**json.loads(_strip_code_fence(content)))

    raise ValueError("triage model returned no tool call and no content")


def _strip_code_fence(text: str) -> str:
    """Remove a leading/trailing markdown code fence if present."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # drop the opening fence line (``` or ```json) and any trailing fence
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else stripped
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[: -3]
    return stripped.strip()


def build_registry(agent_name: str) -> Dict[str, Callable[..., Any]]:  # noqa: ARG001
    """Return the triage task callables keyed by tool name."""
    return {TASK_TOOL_NAME: triage_screen}


__all__ = ["triage_screen", "build_registry"]
