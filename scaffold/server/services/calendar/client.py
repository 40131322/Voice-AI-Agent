"""Composio Google Calendar client (copy of services/gmail/client.py).

To implement: copy services/gmail/client.py verbatim and rename gmail -> calendar
throughout. The Composio client singleton, profile cache, and active-user-id
handling are identical; only the auth-config setting and the connect flow differ.

Keep the SAME shape so tools/calendar.py can call execute_calendar_tool exactly
like tools/gmail.py calls execute_gmail_tool.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

try:  # pragma: no cover - import shim
    from ...config import Settings, get_settings
    from ...logging_config import logger
except Exception:  # standalone linting fallback
    import logging

    logger = logging.getLogger("calendar.client")
    Settings = Any  # type: ignore

    def get_settings():  # type: ignore
        raise NotImplementedError


_ACTIVE_USER_ID_LOCK = threading.Lock()
_ACTIVE_USER_ID: Optional[str] = None


def _set_active_calendar_user_id(user_id: Optional[str]) -> None:
    with _ACTIVE_USER_ID_LOCK:
        global _ACTIVE_USER_ID
        _ACTIVE_USER_ID = (user_id or "").strip() or None


def get_active_calendar_user_id() -> Optional[str]:
    """Return the Composio user id for the connected Google Calendar account."""
    with _ACTIVE_USER_ID_LOCK:
        return _ACTIVE_USER_ID


def _get_composio_client(settings: "Settings" = None):
    """Singleton Composio client — copy the exact body from gmail/client.py."""
    # TODO: copy _get_composio_client from services/gmail/client.py (thread-safe
    # singleton, api_key handling). Gmail and Calendar can share one client.
    raise NotImplementedError("Copy _get_composio_client from services/gmail/client.py")


def execute_calendar_tool(
    tool_name: str,
    composio_user_id: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute a Composio Google Calendar action.

    Mirror execute_gmail_tool: resolve the client, call the Composio action by
    slug, return the raw result dict.
    """
    # TODO: copy execute_gmail_tool body; it is integration-agnostic aside from
    # the action slugs, which the caller (tools/calendar.py) supplies.
    raise NotImplementedError("Copy execute_gmail_tool from services/gmail/client.py")


# --- Connect / status / disconnect (mirror routes/gmail.py handlers) ---------
def initiate_connect(payload: Any, settings: "Settings" = None) -> Any:
    """Start the Calendar OAuth flow. Copy initiate_connect from gmail/client.py,
    swapping composio_gmail_auth_config_id -> composio_calendar_auth_config_id."""
    raise NotImplementedError


def fetch_status(payload: Any) -> Any:
    """Return Calendar connection status. Copy from gmail/client.py."""
    raise NotImplementedError


def disconnect_account(payload: Any) -> Any:
    """Disconnect the Calendar account. Copy from gmail/client.py."""
    raise NotImplementedError


__all__ = [
    "execute_calendar_tool",
    "get_active_calendar_user_id",
    "initiate_connect",
    "fetch_status",
    "disconnect_account",
]
