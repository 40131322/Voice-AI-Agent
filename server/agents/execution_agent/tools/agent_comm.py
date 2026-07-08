"""Inter-agent communication tool for execution agents.

Lets one specialized execution agent hand a sub-task to another and receive its
reply inline. For example, the ``calendar-agent`` can ask the ``gmail-agent`` to
send an invitation email, and the ``gmail-agent`` can ask the ``calendar-agent``
to find a free slot.

The call runs the target agent synchronously (awaited) and returns its response,
bounded by a recursion-depth guard so two agents cannot loop forever.
"""

from __future__ import annotations

import contextvars
from typing import Any, Callable, Dict, List

from server.logging_config import logger
from server.services.execution import get_agent_roster, get_execution_agent_logs

# Track nested inter-agent calls per async context to prevent infinite loops.
_CALL_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar("agent_call_depth", default=0)
MAX_CALL_DEPTH = 3

_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "message_agent",
            "description": (
                "Delegate a sub-task to another specialized agent and get its reply. "
                "Use 'gmail-agent' for sending/searching email and 'calendar-agent' for "
                "calendar/scheduling. Returns the target agent's response so you can continue."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_agent": {
                        "type": "string",
                        "description": "Name of the agent to message, e.g. 'gmail-agent' or 'calendar-agent'.",
                    },
                    "instructions": {
                        "type": "string",
                        "description": "Clear, self-contained instructions for the target agent, including any context it needs.",
                    },
                },
                "required": ["target_agent", "instructions"],
                "additionalProperties": False,
            },
        },
    },
]


def get_schemas() -> List[Dict[str, Any]]:
    """Return inter-agent communication tool schemas."""
    return _SCHEMAS


async def message_agent(target_agent: str, instructions: str) -> Dict[str, Any]:
    """Run another execution agent and return its response inline."""
    target = (target_agent or "").strip()
    if not target:
        return {"error": "target_agent is required."}

    depth = _CALL_DEPTH.get()
    if depth >= MAX_CALL_DEPTH:
        return {
            "error": f"Max inter-agent call depth ({MAX_CALL_DEPTH}) reached; refusing to delegate further.",
        }

    # Ensure the target exists in the roster and log the request for observability.
    roster = get_agent_roster()
    roster.load()
    if target not in set(roster.get_agents()):
        roster.add_agent(target)
    get_execution_agent_logs().record_request(target, instructions)

    # Import lazily to avoid a circular import (runtime imports the tool registry).
    from ..runtime import ExecutionAgentRuntime

    token = _CALL_DEPTH.set(depth + 1)
    try:
        logger.info(f"[inter-agent] delegating to '{target}' (depth {depth + 1})")
        runtime = ExecutionAgentRuntime(agent_name=target)
        result = await runtime.execute(instructions)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(f"[inter-agent] delegation to '{target}' failed")
        return {"target_agent": target, "success": False, "error": str(exc)}
    finally:
        _CALL_DEPTH.reset(token)

    return {
        "target_agent": target,
        "success": result.success,
        "response": result.response,
    }


def build_registry(agent_name: str) -> Dict[str, Callable[..., Any]]:  # noqa: ARG001
    """Return inter-agent communication tool callables."""
    return {"message_agent": message_agent}


__all__ = ["build_registry", "get_schemas", "message_agent", "MAX_CALL_DEPTH"]
