"""Intake Record tools — they live on the INTERACTION agent (by design).

Why here and not on an execution agent: the interaction agent is the one talking
to the caller and receiving each answer. Persisting a field must NOT cost an LLM
round-trip (design doc: "Intake Record is a tool rather than an agent... persisting
a field should not cost an LLM call"). So the interaction agent writes caller
answers directly via record_intake, while the execution agents (Medical, Calendar,
Gmail) READ the same session file through server.services.session.

``request_human_handoff`` lives here for the same reason: when a caller asks for
a person (or the agent is uncertain / the situation is sensitive / a tool
failed), the transfer must happen NOW, without an execution-agent round-trip.
The transfer itself is MOCKED (session write + log) — swap in a real telephony
transfer later.
"""

from __future__ import annotations

from typing import Any, List, Optional

from ...config import get_settings
from ...logging_config import logger
from ...services.session import append_to_session_list, patch_session, read_session
# ToolResult is defined in interaction_agent/tools.py. tools.py imports this module
# at the bottom (after ToolResult is defined), so this import resolves cleanly.
from .tools import ToolResult

HANDOFF_REASONS = [
    "caller_request",     # the caller asked for a human
    "uncertainty",        # triage unmatched / low confidence / needs_human_review
    "sensitive",          # abuse, grief, mental health, legal, billing dispute...
    "tool_failure",       # an agent/tool failed and the flow cannot continue
    "emergency_support",  # caller told to call 911 but needs staff to stay engaged
    "other",
]


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
                    "email": {"type": "string", "description": "Caller email address (used to send the appointment confirmation)."},
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
    {
        "type": "function",
        "function": {
            "name": "request_human_handoff",
            "description": (
                "Transfer this call to a human staff member (mock transfer). Use when: "
                "(1) the caller asks for a person, (2) triage returned needs_human_review "
                "or you are uncertain, (3) the situation is sensitive, or (4) a tool/agent "
                "failed and the flow cannot continue. Writes status='handoff' to the "
                "session — booking is blocked afterwards. Confirm a callback number "
                "first when possible."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {"type": "string", "description": "Active call id."},
                    "reason": {
                        "type": "string",
                        "enum": HANDOFF_REASONS,
                        "description": "Why the call is being handed to a human.",
                    },
                    "details": {
                        "type": "string",
                        "description": "One-line context for the staff member picking up.",
                    },
                },
                "required": ["call_id", "reason"],
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
    email: Optional[str] = None,
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

    caller = {"name": name, "dob": dob, "callback": callback, "email": email, "is_new": is_new}
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


def request_human_handoff(
    call_id: str,
    reason: str,
    details: Optional[str] = None,
) -> ToolResult:
    """Hand the call to a human (mocked transfer). No LLM call, immediate.

    Writes the handoff block + status to the session blackboard. The calendar
    booking guard refuses to book while status == "handoff", so the flow cannot
    silently resume scheduling after a transfer.
    """
    from datetime import datetime, timezone

    if reason not in HANDOFF_REASONS:
        reason = "other"

    # Front-desk line the transfer dials, from config (OPENPOKE_HANDOFF_STAFF_LINE).
    staff_line = get_settings().handoff_staff_line

    session = read_session(call_id)
    callback = (session.get("caller") or {}).get("callback")

    handoff = {
        "requested": True,
        "reason": reason,
        "details": details,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "callback_on_file": callback,
        "transfer_status": "transferred",  # MOCK: real telephony transfer goes here
        "transferred_to": staff_line,
    }
    # Emergency status must keep winning: a handoff during an emergency call
    # still leaves the 911 booking gate engaged.
    patch: dict[str, Any] = {"handoff": handoff}
    if session.get("status") != "emergency":
        patch["status"] = "handoff"
    merged = patch_session(call_id, patch)

    logger.info(
        "[handoff] call %s -> human (reason=%s, callback=%s, details=%s)",
        call_id, reason, callback or "MISSING", details or "-",
    )
    return ToolResult(
        success=True,
        payload={
            "status": "handoff",
            "transferred_to": staff_line,
            "callback_on_file": callback,
            "note": (
                "Mock transfer recorded. Tell the caller they are being connected "
                "to a staff member." + ("" if callback else " No callback number is "
                "on file — ask for one so staff can call back if the line drops.")
            ),
            "session": merged,
        },
    )


# ===========================================================================
# WIRING (into server/agents/interaction_agent/tools.py)
# ===========================================================================
# 1. Add the schemas:
#        from .intake_tools import (
#            INTAKE_TOOL_SCHEMAS, record_intake, read_intake, request_human_handoff,
#        )
#        TOOL_SCHEMAS = [ ...existing..., *INTAKE_TOOL_SCHEMAS ]
#
# 2. Route them in handle_tool_call(), next to send_message_to_agent etc.:
#        if name == "record_intake":
#            return record_intake(**args)
#        if name == "read_intake":
#            return read_intake(**args)
#        if name == "request_human_handoff":
#            return request_human_handoff(**args)
#
# 3. call_id: the interaction agent needs a call id. Simplest MVP: derive one per
#    chat session (e.g. from the conversation/session id) and inject it into the
#    system prompt so the model always passes the same call_id. See todo.md Phase 1.
# ===========================================================================

__all__ = [
    "INTAKE_TOOL_SCHEMAS",
    "HANDOFF_REASONS",
    "record_intake",
    "read_intake",
    "request_human_handoff",
]
