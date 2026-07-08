"""Active-call id lifecycle for the medical-intake demo.

OpenPoke's chat is single-conversation / single-caller (one global conversation
log + one global execution batch state — see the multi-tenant risk note in the
design doc). So "the current call" maps 1:1 to "the current conversation": we
mint one stable ``call_id`` and reuse it for every tool call in the call, and
start a fresh one when the conversation is cleared.

The id is persisted to a small file so it survives a server ``--reload`` mid-call.

Public API:
    get_active_call_id() -> str    stable id for the current call (mints one if none)
    start_new_call()     -> str    mint + persist a fresh id (call on conversation clear)
"""

from __future__ import annotations

import uuid

from .store import SESSIONS_DIR
from ...logging_config import logger

_ACTIVE_CALL_FILE = SESSIONS_DIR / "_active_call.txt"


def _mint() -> str:
    return f"c_{uuid.uuid4().hex[:8]}"


def _read() -> str | None:
    try:
        if _ACTIVE_CALL_FILE.exists():
            value = _ACTIVE_CALL_FILE.read_text(encoding="utf-8").strip()
            return value or None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to read active call id: %s", exc)
    return None


def _write(call_id: str) -> None:
    try:
        _ACTIVE_CALL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ACTIVE_CALL_FILE.write_text(call_id, encoding="utf-8")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to persist active call id: %s", exc)


def get_active_call_id() -> str:
    """Return the current call's id, minting and persisting one if none exists."""
    existing = _read()
    if existing:
        return existing
    call_id = _mint()
    _write(call_id)
    logger.info("Started call (auto) %s", call_id)
    return call_id


def start_new_call() -> str:
    """Mint and persist a fresh call id. Call when a new conversation begins."""
    call_id = _mint()
    _write(call_id)
    logger.info("Started new call %s", call_id)
    return call_id


__all__ = ["get_active_call_id", "start_new_call"]
