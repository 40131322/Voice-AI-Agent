"""Google Calendar tool schemas and actions for the calendar execution agent."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from server.services.calendar import execute_calendar_tool, get_active_calendar_user_id
from server.services.execution import get_execution_agent_logs
from server.services.session import patch_session, read_session

_CALENDAR_AGENT_NAME = "calendar-agent"

_NOT_CONNECTED = "Google Calendar not connected. Please connect Calendar in settings first."
# Safety gates (defense in depth): refuse to book while triage flagged an
# emergency, or after the call was handed to a human.
_EMERGENCY_BLOCKED = (
    "Booking refused: this call is flagged as a medical emergency. "
    "Instruct the caller to call 911; do not schedule an appointment."
)
_HANDOFF_BLOCKED = (
    "Booking refused: this call has been handed off to a human staff member. "
    "The staff member owns scheduling now; do not book."
)


def _emergency_block(call_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return a refusal payload when the session is in emergency/handoff status."""
    if not call_id:
        return None
    status = read_session(call_id).get("status")
    if status in ("emergency", "handoff"):
        _LOG_STORE.record_action(
            _CALENDAR_AGENT_NAME,
            description=f"calendar_create_event BLOCKED ({status}) | call_id={call_id}",
        )
        message = _EMERGENCY_BLOCKED if status == "emergency" else _HANDOFF_BLOCKED
        return {"error": message, "blocked": True}
    return None

_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "calendar_get_current_time",
            "description": "Get the current date and time (optionally in a specific IANA timezone). Call this first to anchor relative dates like 'tomorrow' or 'next week'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone identifier (e.g. 'America/New_York'). Defaults to the account timezone.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_find_events",
            "description": "Search/list events on the user's Google Calendar within an optional time window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text search terms to match against events."},
                    "time_min": {"type": "string", "description": "Lower bound (ISO 8601) for events to return."},
                    "time_max": {"type": "string", "description": "Upper bound (ISO 8601) for events to return."},
                    "max_results": {"type": "integer", "description": "Maximum number of events to return (1-2500)."},
                    "calendar_id": {"type": "string", "description": "Calendar identifier. Defaults to 'primary'."},
                    "single_events": {"type": "boolean", "description": "Expand recurring series into individual instances when true."},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_find_free_slots",
            "description": "Query free/busy information across one or more calendars to find open time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_min": {"type": "string", "description": "Start of the interval to check (ISO 8601)."},
                    "time_max": {"type": "string", "description": "End of the interval to check (ISO 8601)."},
                    "timezone": {"type": "string", "description": "IANA timezone identifier for the query."},
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Calendar identifiers to query. Defaults to the primary calendar.",
                    },
                },
                "required": ["time_min", "time_max"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_create_event",
            "description": "Create a new event on the user's Google Calendar. Pass call_id so the 911 safety gate can refuse to book during a medical emergency.",
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {
                        "type": "string",
                        "description": "Active call id. When the session's triage status is 'emergency', booking is refused.",
                    },
                    "start_datetime": {
                        "type": "string",
                        "description": "REQUIRED. Event start in ISO 8601 (YYYY-MM-DDTHH:MM:SS).",
                    },
                    "end_datetime": {
                        "type": "string",
                        "description": "Event end in ISO 8601. Provide this OR a duration.",
                    },
                    "event_duration_hour": {"type": "integer", "description": "Duration hours (used when end_datetime is omitted)."},
                    "event_duration_minutes": {"type": "integer", "description": "Duration minutes 0-59 (used when end_datetime is omitted)."},
                    "summary": {"type": "string", "description": "Event title."},
                    "description": {"type": "string", "description": "Event description (may contain HTML)."},
                    "location": {"type": "string", "description": "Free-form event location."},
                    "timezone": {"type": "string", "description": "IANA timezone for the event (e.g. 'America/New_York')."},
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Attendee email addresses to invite.",
                    },
                    "calendar_id": {"type": "string", "description": "Target calendar. Defaults to 'primary'."},
                    "create_meeting_room": {"type": "boolean", "description": "Attach a Google Meet link when true."},
                    "send_updates": {"type": "string", "description": "Who to notify: 'all', 'externalOnly', or 'none'."},
                },
                "required": ["start_datetime"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_update_event",
            "description": "Update an existing Google Calendar event by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "REQUIRED. Id of the event to update."},
                    "start_datetime": {"type": "string", "description": "REQUIRED. Event start in ISO 8601 (YYYY-MM-DDTHH:MM:SS)."},
                    "end_datetime": {"type": "string", "description": "Event end in ISO 8601."},
                    "summary": {"type": "string", "description": "Event title."},
                    "description": {"type": "string", "description": "Event description."},
                    "location": {"type": "string", "description": "Event location."},
                    "timezone": {"type": "string", "description": "IANA timezone for the event."},
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Attendee email addresses.",
                    },
                    "calendar_id": {"type": "string", "description": "Calendar the event lives on. Defaults to 'primary'."},
                    "send_updates": {"type": "string", "description": "Who to notify: 'all', 'externalOnly', or 'none'."},
                },
                "required": ["event_id", "start_datetime"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_delete_event",
            "description": "Delete an event from the user's Google Calendar by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "REQUIRED. Id of the event to delete."},
                    "calendar_id": {"type": "string", "description": "Calendar the event lives on. Defaults to 'primary'."},
                    "send_updates": {"type": "string", "description": "Who to notify: 'all', 'externalOnly', or 'none'."},
                },
                "required": ["event_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_quick_add",
            "description": "Create an event from a natural-language phrase (e.g. 'Lunch with Sam tomorrow at noon'). Google parses the details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Natural-language description of the event."},
                    "calendar_id": {"type": "string", "description": "Target calendar. Defaults to 'primary'."},
                    "send_updates": {"type": "string", "description": "Who to notify: 'all', 'externalOnly', or 'none'."},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
    },
]

_LOG_STORE = get_execution_agent_logs()


# --- Medical-intake ("clinic") tools -----------------------------------------
# The clinic flow: read the office's real schedule, offer a MOCK menu of
# appointment slots, then book the chosen one on the REAL Google Calendar. All
# three write to the session-file blackboard so the other agents can coordinate.
_CLINIC_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "clinic_read_schedule",
            "description": "Read the office's existing Google Calendar events (real) and record them on the session so the interaction agent knows what's already booked. Use at connect time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {"type": "string", "description": "Active call id."},
                    "time_min": {"type": "string", "description": "ISO 8601 lower bound. Defaults to now."},
                    "time_max": {"type": "string", "description": "ISO 8601 upper bound. Defaults to ~2 days out."},
                },
                "required": ["call_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clinic_find_slots",
            "description": "Offer ALL real available appointment slots derived from the office's Google Calendar free/busy — every open 30-min gap in working hours (9-5 weekdays, excluding the noon lunch hour), earliest first, no limit — and record them on the session as `availability`. Pass preferred_start/preferred_end (ISO 8601) to restrict results to the caller's requested window (e.g. tomorrow 2-4pm). The caller picks one by slot_id. Returns an empty list if nothing is open.",
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {"type": "string", "description": "Active call id."},
                    "preferred_start": {"type": "string", "description": "Optional ISO 8601 lower bound for the caller's preferred window, e.g. '2026-07-09T14:00:00'. Only slots starting at/after this are returned."},
                    "preferred_end": {"type": "string", "description": "Optional ISO 8601 upper bound for the caller's preferred window, e.g. '2026-07-09T16:00:00'. Only slots ending at/before this are returned."},
                    "urgency": {"type": "integer", "description": "1-5 from triage. Advisory only; does not limit how many slots are returned."},
                },
                "required": ["call_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clinic_book_slot",
            "description": "Book a chosen appointment slot on the REAL Google Calendar and record the booking on the session. Refuses if the call is flagged as a medical emergency; idempotent per call_id + slot_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {"type": "string", "description": "Active call id."},
                    "slot_id": {"type": "string", "description": "Chosen slot id from the offered availability."},
                },
                "required": ["call_id", "slot_id"],
                "additionalProperties": False,
            },
        },
    },
]


# Return Google Calendar tool schemas
def get_schemas() -> List[Dict[str, Any]]:
    """Return Google Calendar tool schemas (generic + clinic)."""

    return [*_SCHEMAS, *_CLINIC_SCHEMAS]


# Execute a Calendar tool and record the action for the execution agent journal
def _execute(tool_name: str, composio_user_id: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    payload = {k: v for k, v in arguments.items() if v is not None}
    payload_str = json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload else "{}"
    try:
        result = execute_calendar_tool(tool_name, composio_user_id, arguments=payload)
    except Exception as exc:
        _LOG_STORE.record_action(
            _CALENDAR_AGENT_NAME,
            description=f"{tool_name} failed | args={payload_str} | error={exc}",
        )
        raise

    _LOG_STORE.record_action(
        _CALENDAR_AGENT_NAME,
        description=f"{tool_name} succeeded | args={payload_str}",
    )
    return result


def _active_user_or_error() -> Optional[str]:
    return get_active_calendar_user_id()


def calendar_get_current_time(timezone: Optional[str] = None) -> Dict[str, Any]:
    composio_user_id = _active_user_or_error()
    if not composio_user_id:
        return {"error": _NOT_CONNECTED}
    return _execute("GOOGLECALENDAR_GET_CURRENT_DATE_TIME", composio_user_id, {"timezone": timezone})


def calendar_find_events(
    query: Optional[str] = None,
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: Optional[int] = None,
    calendar_id: Optional[str] = None,
    single_events: Optional[bool] = None,
) -> Dict[str, Any]:
    composio_user_id = _active_user_or_error()
    if not composio_user_id:
        return {"error": _NOT_CONNECTED}
    arguments = {
        "query": query,
        "time_min": time_min,
        "time_max": time_max,
        "max_results": max_results,
        "calendar_id": calendar_id,
        "single_events": single_events,
    }
    return _execute("GOOGLECALENDAR_FIND_EVENT", composio_user_id, arguments)


def calendar_find_free_slots(
    time_min: str,
    time_max: str,
    timezone: Optional[str] = None,
    items: Optional[List[str]] = None,
) -> Dict[str, Any]:
    composio_user_id = _active_user_or_error()
    if not composio_user_id:
        return {"error": _NOT_CONNECTED}
    arguments = {
        "time_min": time_min,
        "time_max": time_max,
        "timezone": timezone,
        "items": items,
    }
    return _execute("GOOGLECALENDAR_FIND_FREE_SLOTS", composio_user_id, arguments)


def calendar_create_event(
    start_datetime: str,
    end_datetime: Optional[str] = None,
    event_duration_hour: Optional[int] = None,
    event_duration_minutes: Optional[int] = None,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    timezone: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    calendar_id: Optional[str] = None,
    create_meeting_room: Optional[bool] = None,
    send_updates: Optional[str] = None,
    call_id: Optional[str] = None,
) -> Dict[str, Any]:
    # 911 code gate: never book while triage flagged an emergency (prompt rule +
    # this code guard are the two independent layers of the safety gate).
    if blocked := _emergency_block(call_id):
        return blocked
    composio_user_id = _active_user_or_error()
    if not composio_user_id:
        return {"error": _NOT_CONNECTED}
    arguments = {
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
        "event_duration_hour": event_duration_hour,
        "event_duration_minutes": event_duration_minutes,
        "summary": summary,
        "description": description,
        "location": location,
        "timezone": timezone,
        "attendees": attendees,
        "calendar_id": calendar_id,
        "create_meeting_room": create_meeting_room,
        "send_updates": send_updates,
    }
    return _execute("GOOGLECALENDAR_CREATE_EVENT", composio_user_id, arguments)


def calendar_update_event(
    event_id: str,
    start_datetime: str,
    end_datetime: Optional[str] = None,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    timezone: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    calendar_id: Optional[str] = None,
    send_updates: Optional[str] = None,
) -> Dict[str, Any]:
    composio_user_id = _active_user_or_error()
    if not composio_user_id:
        return {"error": _NOT_CONNECTED}
    arguments = {
        "event_id": event_id,
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
        "summary": summary,
        "description": description,
        "location": location,
        "timezone": timezone,
        "attendees": attendees,
        "calendar_id": calendar_id,
        "send_updates": send_updates,
    }
    return _execute("GOOGLECALENDAR_UPDATE_EVENT", composio_user_id, arguments)


def calendar_delete_event(
    event_id: str,
    calendar_id: Optional[str] = None,
    send_updates: Optional[str] = None,
) -> Dict[str, Any]:
    composio_user_id = _active_user_or_error()
    if not composio_user_id:
        return {"error": _NOT_CONNECTED}
    arguments = {
        "event_id": event_id,
        "calendar_id": calendar_id,
        "send_updates": send_updates,
    }
    return _execute("GOOGLECALENDAR_DELETE_EVENT", composio_user_id, arguments)


def calendar_quick_add(
    text: str,
    calendar_id: Optional[str] = None,
    send_updates: Optional[str] = None,
) -> Dict[str, Any]:
    composio_user_id = _active_user_or_error()
    if not composio_user_id:
        return {"error": _NOT_CONNECTED}
    arguments = {
        "text": text,
        "calendar_id": calendar_id,
        "send_updates": send_updates,
    }
    return _execute("GOOGLECALENDAR_QUICK_ADD", composio_user_id, arguments)


# --- Clinic tool implementations ---------------------------------------------
# Fallback menu, used ONLY when the calendar isn't connected or the free/busy
# lookup fails. Real availability is derived from Google Calendar (see
# clinic_find_slots), so these fixed slots are just a demo safety net.
_MOCK_SLOTS: List[Dict[str, Any]] = [
    {"slot_id": "s1", "provider": "Dr. Cheng", "start": "2026-07-08T14:00:00", "end": "2026-07-08T14:30:00"},
    {"slot_id": "s2", "provider": "Dr. Cheng", "start": "2026-07-08T15:00:00", "end": "2026-07-08T15:30:00"},
    {"slot_id": "s3", "provider": "Dr. Cheng", "start": "2026-07-09T09:00:00", "end": "2026-07-09T09:30:00"},
    {"slot_id": "s4", "provider": "Dr. Cheng", "start": "2026-07-09T11:30:00", "end": "2026-07-09T12:00:00"},
]

_DEFAULT_TIMEZONE = "America/New_York"

# Real-availability generation (option 2): free/busy → bookable 30-min slots.
_CLINIC_PROVIDER = "Dr. Cheng"
_WORK_START_HOUR = 9      # first slot starts at 09:00 local
_WORK_END_HOUR = 17       # last slot must end by 17:00 local
_LUNCH_START_HOUR = 12    # office closed 12:00-13:00 for lunch (no slots)
_LUNCH_END_HOUR = 13
_SLOT_MINUTES = 30
_LOOKAHEAD_DAYS = 5       # scan this many days ahead for openings (no per-day cap)


def _extract_events(result: Any) -> List[Dict[str, Any]]:
    """Best-effort pull of an events list out of a Composio find-event response."""
    if isinstance(result, dict):
        for key in ("events", "items", "data"):
            value = result.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = _extract_events(value)
                if nested:
                    return nested
    return []


def _extract_event_id(result: Any) -> Optional[str]:
    """Best-effort search for a created event's id in a Composio response."""
    if isinstance(result, dict):
        # Composio wraps tool output as {"data": ..., "successful": bool, "error": ...}.
        # If the call explicitly failed, there is no real event id to find.
        if result.get("successful") is False:
            return None
        for key in ("id", "event_id", "eventId"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
        for value in result.values():
            found = _extract_event_id(value)
            if found:
                return found
    elif isinstance(result, list):
        for item in result:
            found = _extract_event_id(item)
            if found:
                return found
    return None


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse an RFC3339/ISO timestamp, tolerating a trailing 'Z'."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_local(value: Any, tz: ZoneInfo) -> Optional[datetime]:
    """Parse an ISO timestamp into a tz-aware datetime.

    A naive value (e.g. '2026-07-09T14:00:00', which is how callers express a
    caller's preferred window) is interpreted in the clinic's local timezone.
    """
    dt = _parse_iso(value)
    if dt is None:
        return None
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt


def _extract_busy(result: Any) -> List[Tuple[datetime, datetime]]:
    """Pull busy [start, end] intervals out of a freebusy response (any nesting).

    Google returns {calendars: {<id>: {busy: [{start, end}, ...]}}}; Composio wraps
    that under a "data" key. We walk the whole structure so either shape works.
    """
    busy: List[Tuple[datetime, datetime]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            blocks = node.get("busy")
            if isinstance(blocks, list):
                for interval in blocks:
                    if isinstance(interval, dict):
                        start = _parse_iso(interval.get("start"))
                        end = _parse_iso(interval.get("end"))
                        if start and end:
                            busy.append((start, end))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(result)
    return busy


def _generate_open_slots(
    busy: List[Tuple[datetime, datetime]],
    tz: ZoneInfo,
    now: datetime,
    horizon: datetime,
    pref_start: Optional[datetime] = None,
    pref_end: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Build EVERY open 30-min working-hours slot from ``now`` through ``horizon``.

    No cap is applied — all available slots are returned. If ``pref_start`` /
    ``pref_end`` are given, slots are restricted to that window (a slot must start
    at/after ``pref_start`` and end at/before ``pref_end``).
    """
    slots: List[Dict[str, Any]] = []
    step = timedelta(minutes=_SLOT_MINUTES)
    counter = 0

    num_days = (horizon.date() - now.date()).days
    for day_offset in range(num_days + 1):
        day = (now + timedelta(days=day_offset)).date()
        if day.weekday() >= 5:  # Skip Saturday/Sunday.
            continue
        cursor = datetime(day.year, day.month, day.day, _WORK_START_HOUR, tzinfo=tz)
        day_end = datetime(day.year, day.month, day.day, _WORK_END_HOUR, tzinfo=tz)
        lunch_start = datetime(day.year, day.month, day.day, _LUNCH_START_HOUR, tzinfo=tz)
        lunch_end = datetime(day.year, day.month, day.day, _LUNCH_END_HOUR, tzinfo=tz)
        while cursor + step <= day_end:
            slot_start, slot_end = cursor, cursor + step
            cursor = slot_end
            if slot_start <= now:  # Never offer a slot in the past.
                continue
            # Caller's preferred window (if supplied).
            if pref_start and slot_start < pref_start:
                continue
            if pref_end and slot_end > pref_end:
                continue
            # Office lunch break: skip any slot overlapping 12:00-13:00.
            if slot_start < lunch_end and lunch_start < slot_end:
                continue
            # Overlap test: [start, end) intersects busy [b_start, b_end).
            if any(slot_start < b_end and b_start < slot_end for b_start, b_end in busy):
                continue
            counter += 1
            slots.append(
                {
                    "slot_id": f"s{counter}",
                    "provider": _CLINIC_PROVIDER,
                    # Naive local ISO (no offset): clinic_book_slot passes the
                    # timezone separately to CREATE_EVENT, matching this format.
                    "start": slot_start.strftime("%Y-%m-%dT%H:%M:%S"),
                    "end": slot_end.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )
    return slots


def clinic_read_schedule(
    call_id: str, time_min: Optional[str] = None, time_max: Optional[str] = None
) -> Dict[str, Any]:
    """Read the office's real calendar and record events on the session."""
    user_id = _active_user_or_error()
    if not user_id:
        # Not connected — record an empty schedule so the flow still proceeds.
        patch_session(call_id, {"context": {"existing_events": []}})
        return {"status": "success", "events": [], "warning": _NOT_CONNECTED}

    # NOTE: GOOGLECALENDAR_FIND_EVENT expects snake_case time_min/time_max
    # (verified against the live Composio schema). camelCase keys are silently
    # dropped, which previously voided the requested time window.
    arguments = {
        "time_min": time_min,
        "time_max": time_max,
        "calendar_id": "primary",
        "single_events": True,
        "max_results": 50,
    }
    result = _execute("GOOGLECALENDAR_FIND_EVENT", user_id, arguments)
    events = _extract_events(result)
    patch_session(call_id, {"context": {"existing_events": events}})
    return {"status": "success", "events": events, "count": len(events)}


def clinic_find_slots(
    call_id: str,
    urgency: Optional[int] = None,
    preferred_start: Optional[str] = None,
    preferred_end: Optional[str] = None,
) -> Dict[str, Any]:
    """Derive real appointment slots from the office calendar's free/busy.

    Queries Google Calendar for busy time and returns EVERY open 30-minute slot
    within working hours (9-5 weekdays, minus the noon lunch hour) — no cap. Pass
    ``preferred_start`` / ``preferred_end`` (ISO 8601) to restrict the results to
    the caller's requested window (e.g. tomorrow 2-4pm). Falls back to the mock
    menu only when the calendar isn't connected or the free/busy lookup fails.
    """
    def _fallback(warning: str) -> Dict[str, Any]:
        slots = list(_MOCK_SLOTS)
        patch_session(call_id, {"availability": slots})
        return {"status": "success", "availability": slots, "mock": True, "warning": warning}

    user_id = _active_user_or_error()
    if not user_id:
        return _fallback(_NOT_CONNECTED)

    tz = ZoneInfo(_DEFAULT_TIMEZONE)
    now = datetime.now(tz)
    pref_start = _parse_local(preferred_start, tz)
    pref_end = _parse_local(preferred_end, tz)

    # Scan window: default LOOKAHEAD_DAYS out, but extend to cover a preferred
    # window that reaches further into the future.
    horizon = now + timedelta(days=_LOOKAHEAD_DAYS)
    if pref_end and pref_end > horizon:
        horizon = pref_end
    query_min = pref_start if (pref_start and pref_start > now) else now

    try:
        result = _execute(
            "GOOGLECALENDAR_FIND_FREE_SLOTS",
            user_id,
            {
                "time_min": query_min.isoformat(),
                "time_max": horizon.isoformat(),
                "timezone": _DEFAULT_TIMEZONE,
                "items": ["primary"],
            },
        )
    except Exception:
        return _fallback("free/busy lookup failed")

    busy = _extract_busy(result)
    slots = _generate_open_slots(busy, tz, now, horizon, pref_start, pref_end)

    if not slots:
        # Nothing open in the scanned window — report it rather than inventing slots.
        patch_session(call_id, {"availability": []})
        window = "your requested window" if (pref_start or pref_end) else f"the next {_LOOKAHEAD_DAYS} days"
        return {
            "status": "success",
            "availability": [],
            "mock": False,
            "message": f"No open slots with {_CLINIC_PROVIDER} in {window}.",
        }

    patch_session(call_id, {"availability": slots})
    return {"status": "success", "availability": slots, "mock": False, "count": len(slots)}


def clinic_book_slot(call_id: str, slot_id: str) -> Dict[str, Any]:
    """Book a chosen slot on the real Google Calendar; 911-gated and idempotent."""
    session = read_session(call_id)

    # 911 + handoff code gate (defense in depth: prompt rule + this guard).
    if session.get("status") == "emergency":
        return {"error": _EMERGENCY_BLOCKED, "blocked": True}
    if session.get("status") == "handoff":
        return {"error": _HANDOFF_BLOCKED, "blocked": True}

    # Idempotency: keyed on call_id + slot_id so a retry never double-books.
    existing = session.get("booking") or {}
    if existing.get("slot_id") == slot_id and existing.get("event_id"):
        return {"status": "success", "booking": existing, "idempotent": True}

    slot = next(
        (s for s in session.get("availability", []) if s.get("slot_id") == slot_id),
        None,
    )
    if slot is None:
        return {"error": f"slot_id {slot_id} not found in availability. Call clinic_find_slots first."}

    caller = session.get("caller") or {}
    triage = session.get("triage") or {}
    patient = caller.get("name") or "Patient"
    provider = slot.get("provider") or "Clinic"
    summary = f"Appointment — {patient} with {provider}"
    description = (
        f"Booked via voice intake (call {call_id}). "
        f"Triage: {triage.get('level') or 'n/a'} (urgency {triage.get('urgency') or 'n/a'}). "
        f"Symptoms: {', '.join((session.get('intake') or {}).get('symptoms') or []) or 'n/a'}."
    )

    user_id = _active_user_or_error()
    if not user_id:
        # Graceful mock fallback so a Composio outage can't sink the demo.
        booking = {
            "slot_id": slot_id,
            "event_id": f"mock_evt_{call_id}_{slot_id}",
            "confirmation_id": f"mock_conf_{slot_id}",
        }
        patch_session(call_id, {"booking": booking, "status": "booked"})
        return {"status": "success", "booking": booking, "mock": True, "warning": _NOT_CONNECTED}

    arguments = {
        "start_datetime": slot.get("start"),
        "end_datetime": slot.get("end"),
        "summary": summary,
        "description": description,
        "timezone": _DEFAULT_TIMEZONE,
        "calendar_id": "primary",
    }
    result = _execute("GOOGLECALENDAR_CREATE_EVENT", user_id, arguments)
    event_id = _extract_event_id(result)
    if not event_id:
        # Real path ran but Composio returned no event id (create failed or the
        # response was unexpected). Do NOT mark the session booked — a null
        # event_id "booked" state is a false success. Surface the raw result so
        # the caller can see what happened instead of silently confirming.
        _LOG_STORE.record_action(
            _CALENDAR_AGENT_NAME,
            description=f"clinic_book_slot FAILED (no event id) | call_id={call_id} slot={slot_id}",
        )
        return {
            "status": "error",
            "error": "Google Calendar did not return an event id; the appointment was NOT booked.",
            "result": result,
        }
    booking = {
        "slot_id": slot_id,
        "event_id": event_id,
        "confirmation_id": event_id,
    }
    patch_session(call_id, {"booking": booking, "status": "booked"})
    return {"status": "success", "booking": booking, "event": result}


# Return Google Calendar tool callables
def build_registry(agent_name: str) -> Dict[str, Callable[..., Any]]:  # noqa: ARG001
    """Return Google Calendar tool callables (generic + clinic)."""

    return {
        "calendar_get_current_time": calendar_get_current_time,
        "calendar_find_events": calendar_find_events,
        "calendar_find_free_slots": calendar_find_free_slots,
        "calendar_create_event": calendar_create_event,
        "calendar_update_event": calendar_update_event,
        "calendar_delete_event": calendar_delete_event,
        "calendar_quick_add": calendar_quick_add,
        "clinic_read_schedule": clinic_read_schedule,
        "clinic_find_slots": clinic_find_slots,
        "clinic_book_slot": clinic_book_slot,
    }


__all__ = [
    "build_registry",
    "get_schemas",
    "calendar_get_current_time",
    "calendar_find_events",
    "calendar_find_free_slots",
    "calendar_create_event",
    "calendar_update_event",
    "calendar_delete_event",
    "calendar_quick_add",
    "clinic_read_schedule",
    "clinic_find_slots",
    "clinic_book_slot",
]
