"""Clinic triage task implementation (mirror of tasks/search_email/tool.py).

Priority decision order (explainable and conservative, per the build brief):

1. RULE-BASED DECISION TREE (rules.py) — deterministic, zero-latency, and every
   verdict carries the matched rule ids + the exact path through the tree.
   Levels: emergency (911) / same_day / soon / routine.
2. MODEL FALLBACK — only when symptoms exist but match NO rule, the existing
   LLM screen runs (system_prompt.py). Its verdict is tagged source="model_fallback".
3. CONSERVATIVE DEFAULT — if the model is unavailable/fails, default to "soon"
   and set needs_human_review=True so the voice agent offers a human handoff.

The verdict is written back to session['triage']; on an emergency the session
status flips to "emergency" so the calendar booking guard (tools/calendar.py)
can refuse.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from server.config import get_settings
from server.logging_config import logger
from server.openrouter_client import request_chat_completion
from server.services.execution import get_execution_agent_logs
from server.services.session import patch_session, read_session

from .rules import conservative_default, evaluate_rules
from .schemas import TASK_TOOL_NAME, TriageResult, TriageToolResult
from .system_prompt import get_system_prompt

_LOG_STORE = get_execution_agent_logs()
_TRIAGE_AGENT_NAME = "medical-execution-agent"
_SUBMIT_TOOL_NAME = "submit_triage"

# Completion tool the fallback triage model calls to return a structured verdict.
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
                    "enum": ["emergency", "same_day", "soon", "routine"],
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
    """Screen the current intake and update the session file.

    Rules first; model only as fallback for unmatched symptoms; conservative
    default if the model is unavailable.
    """
    session = read_session(call_id)
    intake = session.get("intake", {})
    caller = session.get("caller", {})

    # ---- 1. Rule-based decision tree (the primary, explainable path) -------
    decision = evaluate_rules(intake.get("symptoms") or [], intake.get("notes"))
    if decision.matched:
        verdict = TriageResult(
            level=decision.level,
            urgency=decision.urgency,
            confidence=decision.confidence,
            rationale=decision.rationale,
            red_flags=decision.red_flags,
            source="rules",
            matched_rules=decision.matched_rules,
            decision_path=decision.decision_path,
            needs_human_review=decision.needs_human_review,
        )
        return _finalize(call_id, verdict)

    # ---- 2. Model fallback (symptoms present, no rule matched) -------------
    settings = get_settings()
    if settings.openrouter_api_key:
        try:
            verdict = await _model_screen(caller, intake, settings.openrouter_api_key)
            verdict.source = "model_fallback"
            verdict.decision_path = decision.decision_path + [
                "[6a] model fallback screen -> " + verdict.level
            ]
            # An unmatched-by-rules verdict is inherently less certain: keep the
            # human-review flag so the voice agent can offer a handoff.
            verdict.needs_human_review = True
            return _finalize(call_id, verdict)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[triage] model fallback failed for %s: %s", call_id, exc)
            _LOG_STORE.record_action(
                _TRIAGE_AGENT_NAME, description=f"triage model fallback failed | {exc}"
            )
    else:
        logger.warning("[triage] no OpenRouter key; using conservative default")

    # ---- 3. Conservative default (never leave the caller unclassified) -----
    decision = conservative_default(decision)
    verdict = TriageResult(
        level=decision.level,
        urgency=decision.urgency,
        confidence=decision.confidence,
        rationale=decision.rationale,
        source="default_conservative",
        matched_rules=decision.matched_rules,
        decision_path=decision.decision_path,
        needs_human_review=True,
    )
    return _finalize(call_id, verdict)


def _finalize(call_id: str, verdict: TriageResult) -> Dict[str, Any]:
    """Write the verdict to the blackboard and build the tool result."""
    patch: Dict[str, Any] = {"triage": verdict.model_dump()}
    if verdict.level == "emergency":
        patch["status"] = "emergency"  # booking guard (tools/calendar.py) reads this
    patch_session(call_id, patch)

    _LOG_STORE.record_action(
        _TRIAGE_AGENT_NAME,
        description=(
            f"triage_screen -> {verdict.level} (urgency {verdict.urgency}, "
            f"source {verdict.source}, rules {verdict.matched_rules or '-'}"
            + (", NEEDS HUMAN REVIEW" if verdict.needs_human_review else "")
            + ")"
        ),
    )
    return TriageToolResult(
        status="success", call_id=call_id, triage=verdict
    ).model_dump(exclude_none=True)


async def _model_screen(
    caller: Dict[str, Any], intake: Dict[str, Any], api_key: str
) -> TriageResult:
    """LLM fallback screen (previous primary path, now used only when rules miss)."""
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
    response = await request_chat_completion(
        model=_model_id(),
        messages=messages,
        system=get_system_prompt(),
        api_key=api_key,
        tools=[_TRIAGE_COMPLETION_SCHEMA],
    )
    return _parse_verdict(response)


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
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def build_registry(agent_name: str) -> Dict[str, Callable[..., Any]]:  # noqa: ARG001
    """Return the triage task callables keyed by tool name."""
    return {TASK_TOOL_NAME: triage_screen}


__all__ = ["triage_screen", "build_registry"]
