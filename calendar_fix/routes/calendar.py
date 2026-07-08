"""Calendar OAuth routes (mirror of routes/gmail.py).

Register in server/app.py:  app.include_router(calendar.router)
The Settings UI can reuse the Gmail connect flow, pointing at /calendar/connect.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..config import Settings, get_settings
from ..models import (
    CalendarConnectPayload,
    CalendarDisconnectPayload,
    CalendarStatusPayload,
)
from ..services import (
    calendar_disconnect_account,
    calendar_fetch_status,
    calendar_initiate_connect,
)

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.post("/connect")
async def calendar_connect(
    payload: CalendarConnectPayload, settings: Settings = Depends(get_settings)
) -> JSONResponse:
    return calendar_initiate_connect(payload, settings)


@router.post("/status")
async def calendar_status(payload: CalendarStatusPayload) -> JSONResponse:
    return calendar_fetch_status(payload)


@router.post("/disconnect")
async def calendar_disconnect(payload: CalendarDisconnectPayload) -> JSONResponse:
    return calendar_disconnect_account(payload)
