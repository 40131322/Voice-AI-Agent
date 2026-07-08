"""Google Calendar service helpers (mirror of services/gmail/).

Composio-backed, same client/_execute pattern as Gmail. This is the ONE real new
integration in the build; everything else is reuse.
"""

from .client import (
    execute_calendar_tool,
    get_active_calendar_user_id,
    initiate_connect,
    fetch_status,
    disconnect_account,
)

__all__ = [
    "execute_calendar_tool",
    "get_active_calendar_user_id",
    "initiate_connect",
    "fetch_status",
    "disconnect_account",
]
