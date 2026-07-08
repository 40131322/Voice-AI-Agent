"""Google Calendar tool schemas and actions for the calendar execution agent."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from server.services.calendar import execute_calendar_tool, get_active_calendar_user_id
from server.services.execution import get_execution_agent_logs

_CALENDAR_AGENT_NAME = "calendar-agent"

_NOT_CONNECTED = "Google Calendar not connected. Please connect Calendar in settings first."

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
            "description": "Create a new event on the user's Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {
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


# Return Google Calendar tool schemas
def get_schemas() -> List[Dict[str, Any]]:
    """Return Google Calendar tool schemas."""

    return _SCHEMAS


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
) -> Dict[str, Any]:
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


# Return Google Calendar tool callables
def build_registry(agent_name: str) -> Dict[str, Callable[..., Any]]:  # noqa: ARG001
    """Return Google Calendar tool callables."""

    return {
        "calendar_get_current_time": calendar_get_current_time,
        "calendar_find_events": calendar_find_events,
        "calendar_find_free_slots": calendar_find_free_slots,
        "calendar_create_event": calendar_create_event,
        "calendar_update_event": calendar_update_event,
        "calendar_delete_event": calendar_delete_event,
        "calendar_quick_add": calendar_quick_add,
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
]
