"""Composio Google Calendar client — REAL implementation (mirror of gmail/client.py).

This is what makes bookings actually land in Google Calendar. Your current
clinic_book_slot only writes the session file (mock), which is why nothing shows
up on the calendar. Wire clinic_* tools to execute_calendar_tool(...) below and
connect the account via the /calendar/connect route.

Toolkit slug: GOOGLECALENDAR. Auth config: settings.composio_calendar_auth_config_id.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Optional

from fastapi import status
from fastapi.responses import JSONResponse

from ...config import Settings, get_settings
from ...logging_config import logger
from ...models import (
    CalendarConnectPayload,
    CalendarDisconnectPayload,
    CalendarStatusPayload,
)
from ...utils import error_response


_CLIENT_LOCK = threading.Lock()
_CLIENT: Optional[Any] = None
_ACTIVE_USER_ID_LOCK = threading.Lock()
_ACTIVE_USER_ID: Optional[str] = None

_TOOLKIT = "GOOGLECALENDAR"


def _normalized(value: Optional[str]) -> str:
    return (value or "").strip()


def _set_active_calendar_user_id(user_id: Optional[str]) -> None:
    sanitized = _normalized(user_id)
    with _ACTIVE_USER_ID_LOCK:
        global _ACTIVE_USER_ID
        _ACTIVE_USER_ID = sanitized or None


def get_active_calendar_user_id() -> Optional[str]:
    with _ACTIVE_USER_ID_LOCK:
        return _ACTIVE_USER_ID


def _import_composio():
    from composio import Composio  # type: ignore
    return Composio


def _get_composio_client(settings: Optional[Settings] = None):
    """Singleton Composio client (Gmail and Calendar can share one)."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            resolved = settings or get_settings()
            Composio = _import_composio()
            api_key = resolved.composio_api_key
            try:
                _CLIENT = Composio(api_key=api_key) if api_key else Composio()
            except TypeError as exc:
                if api_key:
                    raise RuntimeError(
                        "Composio SDK does not accept api_key; upgrade SDK or remove COMPOSIO_API_KEY."
                    ) from exc
                _CLIENT = Composio()
    return _CLIENT


# --- OAuth connect ----------------------------------------------------------
def initiate_connect(payload: CalendarConnectPayload, settings: Settings) -> JSONResponse:
    auth_config_id = payload.auth_config_id or settings.composio_calendar_auth_config_id or ""
    if not auth_config_id:
        return error_response(
            "Missing auth_config_id. Set COMPOSIO_CALENDAR_AUTH_CONFIG_ID or pass auth_config_id.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user_id = payload.user_id or f"web-{os.getpid()}"
    _set_active_calendar_user_id(user_id)
    try:
        client = _get_composio_client(settings)
        req = client.connected_accounts.initiate(user_id=user_id, auth_config_id=auth_config_id)
        return JSONResponse(
            {
                "ok": True,
                "redirect_url": getattr(req, "redirect_url", None) or getattr(req, "redirectUrl", None),
                "connection_request_id": getattr(req, "id", None),
                "user_id": user_id,
            }
        )
    except Exception as exc:
        logger.exception("calendar connect failed", extra={"user_id": user_id})
        return error_response(
            "Failed to initiate Calendar connect",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


# --- Status -----------------------------------------------------------------
def fetch_status(payload: CalendarStatusPayload) -> JSONResponse:
    connection_request_id = _normalized(payload.connection_request_id)
    user_id = _normalized(payload.user_id)
    if not connection_request_id and not user_id:
        return error_response(
            "Missing connection_request_id or user_id",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        client = _get_composio_client()
        account: Any = None
        if connection_request_id:
            try:
                account = client.connected_accounts.wait_for_connection(connection_request_id, timeout=2.0)
            except Exception:
                try:
                    account = client.connected_accounts.get(connection_request_id)
                except Exception:
                    account = None
        if account is None and user_id:
            try:
                items = client.connected_accounts.list(
                    user_ids=[user_id], toolkit_slugs=[_TOOLKIT], statuses=["ACTIVE"]
                )
                data = getattr(items, "data", None)
                if data is None and isinstance(items, dict):
                    data = items.get("data")
                if data:
                    account = data[0]
            except Exception:
                account = None

        status_value = None
        connected = False
        account_user_id = None
        if account is not None:
            status_value = getattr(account, "status", None) or (
                account.get("status") if isinstance(account, dict) else None
            )
            connected = (status_value or "").upper() in {
                "CONNECTED", "SUCCESS", "SUCCESSFUL", "ACTIVE", "COMPLETED",
            }
            account_user_id = getattr(account, "user_id", None) or (
                account.get("user_id") if isinstance(account, dict) else None
            )

        if not user_id and account_user_id:
            user_id = _normalized(account_user_id)
        _set_active_calendar_user_id(user_id)

        return JSONResponse(
            {
                "ok": True,
                "connected": bool(connected),
                "status": status_value or "UNKNOWN",
                "user_id": user_id,
            }
        )
    except Exception as exc:
        logger.exception("calendar status failed", extra={"user_id": user_id})
        return error_response(
            "Failed to fetch calendar status",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


# --- Disconnect -------------------------------------------------------------
def disconnect_account(payload: CalendarDisconnectPayload) -> JSONResponse:
    connection_id = _normalized(payload.connection_id) or _normalized(payload.connection_request_id)
    user_id = _normalized(payload.user_id)
    if not connection_id and not user_id:
        return error_response(
            "Missing connection_id or user_id", status_code=status.HTTP_400_BAD_REQUEST
        )

    try:
        client = _get_composio_client()
    except Exception as exc:
        return error_response(
            "Failed to disconnect Calendar",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    removed: list[str] = []
    if connection_id:
        try:
            client.connected_accounts.delete(connection_id)
            removed.append(connection_id)
        except Exception as exc:
            logger.exception("calendar disconnect failed", extra={"connection_id": connection_id})
            return error_response(
                "Failed to disconnect Calendar",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(exc),
            )
    else:
        try:
            items = client.connected_accounts.list(user_ids=[user_id], toolkit_slugs=[_TOOLKIT])
            data = getattr(items, "data", None) or (items.get("data") if isinstance(items, dict) else None)
            for entry in data or []:
                cid = getattr(entry, "id", None) or (entry.get("id") if isinstance(entry, dict) else None)
                if cid:
                    try:
                        client.connected_accounts.delete(cid)
                        removed.append(cid)
                    except Exception:
                        logger.exception("failed removing calendar connection", extra={"cid": cid})
        except Exception as exc:
            return error_response(
                "Failed to disconnect Calendar",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(exc),
            )

    if get_active_calendar_user_id() == user_id:
        _set_active_calendar_user_id(None)

    return JSONResponse({"ok": True, "disconnected": bool(removed), "removed_connection_ids": removed})


# --- Tool execution (this is the call that actually books) ------------------
def _normalize_tool_response(result: Any) -> Dict[str, Any]:
    payload: Optional[Dict[str, Any]] = None
    try:
        if hasattr(result, "model_dump"):
            payload = result.model_dump()
        elif hasattr(result, "dict"):
            payload = result.dict()
    except Exception:
        payload = None
    if payload is None:
        try:
            if hasattr(result, "model_dump_json"):
                payload = json.loads(result.model_dump_json())
        except Exception:
            payload = None
    if payload is None:
        if isinstance(result, dict):
            payload = result
        elif isinstance(result, list):
            payload = {"items": result}
        else:
            payload = {"repr": str(result)}
    return payload


def execute_calendar_tool(
    tool_name: str,
    composio_user_id: str,
    *,
    arguments: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute a Composio Google Calendar action for the connected account.

    Note: unlike Gmail (which injects user_id="me"), Calendar actions take a
    calendar_id (default "primary"), so we do NOT force user_id into arguments.
    """
    prepared: Dict[str, Any] = {}
    if isinstance(arguments, dict):
        for key, value in arguments.items():
            if value is not None:
                prepared[key] = value

    try:
        client = _get_composio_client()
        result = client.client.tools.execute(
            tool_name,
            user_id=composio_user_id,
            arguments=prepared,
        )
        return _normalize_tool_response(result)
    except Exception as exc:
        logger.exception(
            "calendar tool execution failed",
            extra={"tool": tool_name, "user_id": composio_user_id},
        )
        raise RuntimeError(f"{tool_name} invocation failed: {exc}") from exc


__all__ = [
    "execute_calendar_tool",
    "get_active_calendar_user_id",
    "initiate_connect",
    "fetch_status",
    "disconnect_account",
]
