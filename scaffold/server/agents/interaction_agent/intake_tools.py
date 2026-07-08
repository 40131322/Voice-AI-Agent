"""Intake Record tools — they live on the INTERACTION agent (by design).

Why here and not on an execution agent: the interaction agent is the one talking
to the caller and receiving each answer. Persisting a field must NOT cost an LLM
round-trip (design doc: "Intake Record is a tool rather than an agent... persisting
a field should not cost an LLM call"). So the interaction agent writes caller
answers directly via record_intake, while the execution agents (Medical, Calendar,
Gmail) READ the same session file through server.services.session.

These schemas/handlers are meant to be merged into
server/agents/interaction_agent/tools.py — see WIRING notes at the bottom.
"""

from __future__ import annotations

from typing import Any, List, Optional

from ...services.session import append_to_session_list, patch_session, read_session
# ToolResult is defined in interaction_agent/tools.py. When you merge this in,
# drop this import and use the local ToolResult dataclass.
from .tools import ToolResult  # type: ignore


# --- Tool schemas (append to TOOL_SCHEMAS in tools.py) ----------------------
INTAKE_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "record_intake",
            "description": (
                "Silently save a caller's answer to the shared session file. Call this "
                "after every substantive answer BEFORE asking the Medical Agent to re-screen. "
                "Does not talk to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {"type": "string", "description": "Active call id."},
                    "name": {"type": "string", "description": "Caller full name."},
                    "dob": {"type": "string", "description": "Date of birth."},
                    "callback": {"type": "string", "description": "Callback phone number."},
                    "is_new": {"type": "boolean", "description": "True if a new patient."},
                    "symptoms": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Symptoms mentioned so far (full list, replaces prior).",
                    },
                    "insurance": {"type": "string", "description": "Insurance provider."},
                    "notes": {"type": "string", "description": "Any free-text notes."},
                },
                "required": ["call_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_intake",
            "description": "Read the current session file for a call (caller, intake, triage, booking).",
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {"type": "string", "description": "Active call id."},
                },
                "required": ["call_id"],
                "additionalProperties": False,
            },
        },
    },
]


# --- Handlers ---------------------------------------------------------------
def record_intake(
    call_id: str,
    name: Optional[str] = None,
    dob: Optional[str] = None,
    callback: Optional[str] = None,
    is_new: Optional[bool] = None,
    symptoms: Optional[List[str]] = None,
    insurance: Optional[str] = None,
    notes: Optional[str] = None,
) -> ToolResult:
    """Write caller answers into the session-file blackboard (no LLM call)."""
    from datetime import datetime, timezone

    # Symptoms are appended atomically (see store.append_to_session_list) so a
    # concurrent triage read can never race away a symptom.
    if symptoms:
        append_to_session_list(call_id, "intake.symptoms", *symptoms)

    caller = {"name": name, "dob": dob, "callback": callback, "is_new": is_new}
    intake = {"insurance": insurance, "notes": notes}
    caller = {k: v for k, v in caller.items() if v is not None}
    intake = {k: v for k, v in intake.items() if v is not None}

    patch: dict[str, Any] = {}
    if caller:
        patch["caller"] = caller
    if intake or symptoms:
        intake["updated_at"] = datetime.now(timezone.utc).isoformat()
        patch["intake"] = intake

    merged = patch_session(call_id, patch) if patch else read_session(call_id)
    return ToolResult(success=True, payload={"status": "recorded", "session": merged})


def read_intake(call_id: str) -> ToolResult:
    """Return the current session dict for a call."""
    return ToolResult(success=True, payload={"session": read_session(call_id)})


# ===========================================================================
# WIRING (into server/agents/interaction_agent/tools.py)
# ===========================================================================
# 1. Add the schemas:
#        from .intake_tools import INTAKE_TOOL_SCHEMAS, record_intake, read_intake
#        TOOL_SCHEMAS = [ ...existing..., *INTAKE_TOOL_SCHEMAS ]
#
# 2. Route them in handle_tool_call(), next to send_message_to_agent etc.:
#        if name == "record_intake":
#            return record_intake(**args)
#        if name == "read_intake":
#            return read_intake(**args)
#
# 3. call_id: the interaction agent needs a call id. Simplest MVP: derive one per
#    chat session (e.g. from the conversation/session id) and inject it into the
#    system prompt so the model always passes the same call_id. See todo.md Phase 1.
# ===========================================================================

__all__ = ["INTAKE_TOOL_SCHEMAS", "record_intake", "read_intake"]
