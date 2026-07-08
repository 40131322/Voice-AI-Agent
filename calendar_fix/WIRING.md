# Make bookings actually hit Google Calendar

Your `clinic_book_slot` currently writes only the session file, so nothing reaches
Google Calendar. These are the exact steps to switch it to real Composio bookings.

## 1. Drop in the files (mirror the Gmail wiring)

- `services/calendar/client.py`   → `server/services/calendar/client.py`
- `models/calendar.py`            → `server/models/calendar.py`
- `routes/calendar.py`            → `server/routes/calendar.py`

## 2. Register them (4 small edits)

**server/models/__init__.py** — export the payloads next to the Gmail ones:
```python
from .calendar import (
    CalendarConnectPayload, CalendarStatusPayload, CalendarDisconnectPayload,
)
```

**server/services/__init__.py** — export the calendar fns (alias to avoid clashing
with the Gmail names of the same shape):
```python
from .calendar.client import (
    execute_calendar_tool,
    get_active_calendar_user_id,
    initiate_connect as calendar_initiate_connect,
    fetch_status as calendar_fetch_status,
    disconnect_account as calendar_disconnect_account,
)
```

**server/app.py** — include the router next to the gmail one:
```python
from .routes import calendar as calendar_routes
app.include_router(calendar_routes.router)
```

**server/config.py** — add the auth-config setting (mirror the Gmail line):
```python
composio_calendar_auth_config_id: Optional[str] = Field(
    default=os.getenv("COMPOSIO_CALENDAR_AUTH_CONFIG_ID")
)
```

## 3. Composio dashboard + .env

1. In Composio, add the **Google Calendar** toolkit and create an **auth config**.
2. Copy its id into `.env`:
   ```
   COMPOSIO_CALENDAR_AUTH_CONFIG_ID=ac_xxxxxxxx
   ```
3. Confirm the exact action slugs in the dashboard (they drift): `GOOGLECALENDAR_FIND_EVENTS`,
   `GOOGLECALENDAR_FIND_FREE_SLOTS`, `GOOGLECALENDAR_CREATE_EVENT`.

## 4. Connect the account

Restart the server, then connect Google Calendar via OAuth. Either add a "Calendar"
button in Settings pointing at `/calendar/connect` (mirror the Gmail button), or
connect once with curl:
```bash
curl -s -X POST localhost:8001/calendar/connect -H 'content-type: application/json' -d '{}'
# open the returned redirect_url, approve, then:
curl -s -X POST localhost:8001/calendar/status  -H 'content-type: application/json' \
  -d '{"connection_request_id":"<id from connect>"}'
# expect {"connected": true, ...}
```
`get_active_calendar_user_id()` now returns a real id, so tool calls will authenticate.

## 5. Flip your clinic_* tools from mock to real

Replace the mock bodies. These use the session store for state + Composio for the
real write. Adjust arg names to the slugs your dashboard shows.

```python
from server.services.calendar import execute_calendar_tool, get_active_calendar_user_id
from server.services.session import read_session, patch_session

def clinic_read_schedule(call_id: str, time_min=None, time_max=None):
    uid = get_active_calendar_user_id()
    if not uid:
        return {"error": "Calendar not connected. Connect Google Calendar in settings first."}
    res = execute_calendar_tool("GOOGLECALENDAR_FIND_EVENTS", uid, arguments={
        "calendar_id": "primary", "timeMin": time_min, "timeMax": time_max,
    })
    events = res.get("data", res)          # shape varies — inspect once and parse
    patch_session(call_id, {"context": {"existing_events": events}})
    return {"status": "success", "events": events}

def clinic_find_slots(call_id: str, urgency: int | None = None):
    uid = get_active_calendar_user_id()
    if not uid:
        return {"error": "Calendar not connected."}
    res = execute_calendar_tool("GOOGLECALENDAR_FIND_FREE_SLOTS", uid, arguments={
        "calendar_id": "primary",
        # pass a time window; earlier window for higher urgency
    })
    slots = res.get("data", res)
    patch_session(call_id, {"availability": slots})
    return {"status": "success", "availability": slots}

def clinic_book_slot(call_id: str, slot_id: str):
    session = read_session(call_id)

    # 911 guard (defense in depth): never book during an emergency
    if session.get("status") == "emergency":
        return {"error": "Booking refused: caller is in a medical emergency."}

    # idempotency: same slot already booked -> return existing, no double-book
    existing = session.get("booking", {})
    if existing.get("slot_id") == slot_id and existing.get("event_id"):
        return {"status": "success", "booking": existing, "idempotent": True}

    slot = next((s for s in session.get("availability", []) if s.get("slot_id") == slot_id), None)
    if slot is None:
        return {"error": f"slot_id {slot_id} not in availability."}

    uid = get_active_calendar_user_id()
    if not uid:
        return {"error": "Calendar not connected."}

    caller = session.get("caller", {})
    res = execute_calendar_tool("GOOGLECALENDAR_CREATE_EVENT", uid, arguments={
        "calendar_id": "primary",
        "summary": f"Appointment — {caller.get('name') or 'Patient'}",
        "start_datetime": slot["start"],       # e.g. "2026-07-08T14:00:00"
        "event_duration_hour": 0,
        "event_duration_minutes": 30,
        "description": f"call_id={call_id}; slot_id={slot_id}",  # audit / idempotency key
    })

    event_id = (res.get("data") or res).get("id") or (res.get("data") or res).get("event_id")
    booking = {"slot_id": slot_id, "event_id": event_id, "confirmation_id": event_id}
    patch_session(call_id, {"booking": booking, "status": "booked"})
    return {"status": "success", "booking": booking}
```

## 6. Verify

Run scenario 1. You should now see the event appear in Google Calendar, and the
session file's `booking.event_id` should be a real Google id (starts with a long
alphanumeric), not `evt_c_...`. If `clinic_book_slot` returns "Calendar not
connected", step 4 didn't take — re-check `/calendar/status`.

## Two behavior fixes noticed in your last run

- **Book only after the caller picks a slot.** The agent called `clinic_book_slot`
  before confirmation and booked twice (idempotency caught the dupe). Add to the
  interaction prompt: "Never call the booking tool until the caller has explicitly
  chosen a specific slot."
- **`GOOGLECALENDAR_FIND_EVENTS`** (plural) is usually the read-schedule slug;
  `FIND_EVENT` singular may not exist. Confirm in the dashboard.
```
