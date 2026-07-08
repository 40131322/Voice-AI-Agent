"""Google Calendar tool schemas and actions (mirror of tools/gmail.py).

The Calendar Agent uses these to read the schedule, find free slots, and book.
Includes the defense-in-depth 911 guard and idempotent booking required by the
design doc. Register in tools/registry.py next to gmail.

Confirm the exact Composio action slugs in your Composio dashboard — they change.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from server.services.execution import get_execution_agent_logs
from server.services.calendar import execute_calendar_tool, get_active_calendar_user_id
from server.services.session import patch_session, read_session

_CALENDAR_AGENT_NAME = "calendar-execution-agent"

# Composio Google Calendar action slugs — VERIFY in the dashboard.
ACTION_FIND_EVENT = "GOOGLECALENDAR_FIND_EVENT"
ACTION_FIND_FREE_SLOTS = "GOOGLECALENDAR_FIND_FREE_SLOTS"  # or a free/busy query
ACTION_CREATE_EVENT = "GOOGLECALENDAR_CREATE_EVENT"

# Flip to True to run the demo without Composio Calendar configured.
USE_MOCK = True

_LOG_STORE = get_execution_agent_logs()

_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "calendar_read_schedule",
            "description": "Read today's/tomorrow's existing events for the office calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {"type": "string", "description": "Active call id."},
                    "time_min": {"type": "string", "description": "ISO start of range."},
                    "time_max": {"type": "string", "description": "ISO end of range."},
                },
                "required": ["call_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_find_free_slots",
            "description": "Find open appointment slots, prioritized by triage urgency.",
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {"type": "string", "description": "Active call id."},
                    "urgency": {"type": "integer", "description": "1-5 from triage."},
                },
                "required": ["call_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_create_event",
            "description": "Book an appointment for a chosen slot. Refuses during a medical emergency.",
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {"type": "string", "description": "Active call id."},
                    "slot_id": {"type": "string", "description": "Chosen slot id from availability."},
                },
                "required": ["call_id", "slot_id"],
                "additionalProperties": False,
            },
        },
    },
]


def get_schemas() -> List[Dict[str, Any]]:
    return _SCHEMAS


def _execute(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Run a Composio Calendar action and journal it (mirror gmail._execute)."""
    payload = {k: v for k, v in arguments.items() if v is not None}
    user_id = get_active_calendar_user_id()
    if not user_id:
        return {"error": "Calendar not connected. Connect Google Calendar in settings first."}
    try:
        result = execute_calendar_tool(tool_name, user_id, arguments=payload)
    except Exception as exc:
        _LOG_STORE.record_action(_CALENDAR_AGENT_NAME, description=f"{tool_name} failed | {exc}")
        raise
    _LOG_STORE.record_action(_CALENDAR_AGENT_NAME, description=f"{tool_name} succeeded")
    return result


# --- Tool callables ---------------------------------------------------------
def calendar_read_schedule(
    call_id: str, time_min: Optional[str] = None, time_max: Optional[str] = None
) -> Dict[str, Any]:
    if USE_MOCK:
        events = [{"start": "2026-07-08T09:00", "end": "2026-07-08T09:30", "title": "Existing patient"}]
        patch_session(call_id, {"context": {"existing_events": events}})
        return {"status": "success", "events": events, "mock": True}
    # TODO: result = _execute(ACTION_FIND_EVENT, {...}); parse -> events
    events: List[Dict[str, Any]] = []
    patch_session(call_id, {"context": {"existing_events": events}})
    return {"status": "success", "events": events}


def calendar_find_free_slots(call_id: str, urgency: Optional[int] = None) -> Dict[str, Any]:
    if USE_MOCK:
        slots = [
            {"slot_id": "s1", "provider": "Dr. Lee", "start": "2026-07-08T14:00", "end": "2026-07-08T14:30"},
            {"slot_id": "s2", "provider": "Dr. Lee", "start": "2026-07-08T15:00", "end": "2026-07-08T15:30"},
        ]
        patch_session(call_id, {"availability": slots})
        return {"status": "success", "availability": slots, "mock": True}
    # TODO: result = _execute(ACTION_FIND_FREE_SLOTS, {...}); higher urgency -> earlier slots
    slots: List[Dict[str, Any]] = []
    patch_session(call_id, {"availability": slots})
    return {"status": "success", "availability": slots}


def calendar_create_event(call_id: str, slot_id: str) -> Dict[str, Any]:
    session = read_session(call_id)

    # --- 911 code gate (defense in depth: prompt rule + this guard) ---
    if session.get("status") == "emergency":
        return {"error": "Booking refused: caller is in a medical emergency. Instruct them to call 911."}

    # --- Idempotency: key on call_id + slot_id so a retry never double-books ---
    existing = session.get("booking", {})
    if existing.get("slot_id") == slot_id and existing.get("event_id"):
        return {"status": "success", "booking": existing, "idempotent": True}

    slot = next((s for s in session.get("availability", []) if s.get("slot_id") == slot_id), None)
    if slot is None:
        return {"error": f"slot_id {slot_id} not found in availability."}

    if USE_MOCK:
        event_id = f"evt_{call_id}_{slot_id}"
        booking = {"slot_id": slot_id, "event_id": event_id, "confirmation_id": f"conf_{event_id}"}
        patch_session(call_id, {"booking": booking, "status": "booked"})
        return {"status": "success", "booking": booking, "mock": True}

    # TODO: result = _execute(ACTION_CREATE_EVENT, {idempotency_key: f"{call_id}:{slot_id}", ...})
    booking = {"slot_id": slot_id, "event_id": None, "confirmation_id": None}
    patch_session(call_id, {"booking": booking, "status": "booked"})
    return {"status": "success", "booking": booking}


def build_registry(agent_name: str) -> Dict[str, Callable[..., Any]]:  # noqa: ARG001
    return {
        "calendar_read_schedule": calendar_read_schedule,
        "calendar_find_free_slots": calendar_find_free_slots,
        "calendar_create_event": calendar_create_event,
    }


__all__ = ["get_schemas", "build_registry"]
