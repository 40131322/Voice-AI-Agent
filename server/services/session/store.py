"""Concurrency-safe session-file store (the "blackboard").

Copies the fcntl.LOCK_EX pattern proven in ``server/services/execution/roster.py``.
Every agent (Medical, Calendar, Gmail) reads from and writes to a single per-call
JSON file, so parallel agents never block on each other's prompt text.

Path: server/data/sessions/<call_id>.json   (server/data/ is git-ignored)

Public API:
    read_session(call_id)         -> dict           (full session, defaults if absent)
    patch_session(call_id, patch) -> dict           (deep-merge under exclusive lock)
    default_session(call_id)      -> dict           (fresh skeleton)
    session_path(call_id)         -> Path

TODO(before demo):
    - Wire SESSIONS_DIR to the same data root the rest of the app uses.
    - Add a light unit test that patches from two threads and asserts no lost writes.
"""

from __future__ import annotations

import copy
import fcntl
import json
import time
from pathlib import Path
from typing import Any, Dict

# NOTE: adjust relative depth if you move this file. From
# server/services/session/store.py the project root is three parents up.
try:  # pragma: no cover - import shim so the stub can be dropped in as-is
    from ...logging_config import logger
except Exception:  # fallback for standalone linting
    import logging

    logger = logging.getLogger("session.store")


# server/data/sessions/
SESSIONS_DIR = Path(__file__).resolve().parents[2] / "data" / "sessions"


# ---------------------------------------------------------------------------
# Skeleton
# ---------------------------------------------------------------------------
def default_session(call_id: str) -> Dict[str, Any]:
    """Return a fresh session skeleton (design doc, section Coordination)."""
    return {
        "call_id": call_id,
        "status": "intake",  # intake | emergency | scheduling | booked | handoff
        "caller": {"name": None, "dob": None, "callback": None, "is_new": None},
        "intake": {"symptoms": [], "insurance": None, "notes": None, "updated_at": None},
        # level: emergency | same_day | soon | routine (rule-based decision tree;
        # see execution_agent/tasks/clinic/rules.py). source/matched_rules/
        # decision_path/needs_human_review are the explainability trail.
        "triage": {
            "level": None,
            "urgency": None,
            "confidence": None,
            "rationale": None,
            "source": None,
            "matched_rules": [],
            "decision_path": [],
            "needs_human_review": False,
        },
        "context": {"prior_visits": [], "existing_events": []},
        "availability": [],  # [{slot_id, provider, start, end}]
        "booking": {"slot_id": None, "event_id": None, "confirmation_id": None},
        "confirmation": {"email_status": None, "message_id": None},
        # Human handoff path (request_human_handoff on the interaction agent).
        "handoff": {
            "requested": False,
            "reason": None,
            "details": None,
            "requested_at": None,
            "callback_on_file": None,
            "transfer_status": None,
            "transferred_to": None,
        },
    }


def session_path(call_id: str) -> Path:
    """Return the on-disk path for a call's session file."""
    safe = "".join(c for c in call_id if c.isalnum() or c in ("_", "-"))
    return SESSIONS_DIR / f"{safe}.json"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def read_session(call_id: str) -> Dict[str, Any]:
    """Read a session file, returning the default skeleton if it does not exist.

    Reads are cheap and do not take the exclusive lock (matches roster.py, which
    only locks on write). If you observe torn reads under heavy concurrency,
    switch to a shared lock (fcntl.LOCK_SH) here.
    """
    path = session_path(call_id)
    if not path.exists():
        return default_session(call_id)
    try:
        # Take a SHARED lock so we never read the file mid-truncate while a writer
        # holds the exclusive lock (avoids torn/empty reads under contention).
        with open(path, "r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                raw = f.read().strip()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        if not raw:
            return default_session(call_id)
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        logger.warning("session %s was not a dict; resetting", call_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to read session %s: %s", call_id, exc)
    return default_session(call_id)


# ---------------------------------------------------------------------------
# Write (deep-merge under exclusive lock)
# ---------------------------------------------------------------------------
def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``patch`` into ``base`` and return ``base``.

    Dict values merge key-by-key; every other type (including lists) is replaced.
    If you need list-append semantics (e.g. adding one symptom at a time), do the
    read-append in the caller and pass the full list here.
    """
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def patch_session(call_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Read-modify-write a session file under an exclusive fcntl lock.

    Serializes concurrent writers so parallel agents never lose each other's
    updates. Returns the merged session dict.
    """
    path = session_path(call_id)
    max_retries = 3

    for attempt in range(max_retries):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            # Open for read+write, create if missing. Use a BLOCKING exclusive
            # lock so concurrent writers queue instead of dropping writes — the
            # read-modify-write must be serialized, and appointment-booking writes
            # are far too important to silently lose under contention.
            with open(path, "a+", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # waits its turn
                try:
                    f.seek(0)
                    raw = f.read().strip()
                    current = json.loads(raw) if raw else default_session(call_id)
                    if not isinstance(current, dict):
                        current = default_session(call_id)

                    merged = _deep_merge(copy.deepcopy(current), patch)

                    f.seek(0)
                    f.truncate()
                    json.dump(merged, f, indent=2)
                    f.flush()
                    return merged
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to patch session %s (attempt %d): %s", call_id, attempt + 1, exc
            )
            time.sleep(0.05)

    # Best-effort fallback: return current on-disk state so callers don't crash.
    logger.error("patch_session gave up for %s", call_id)
    return read_session(call_id)


def append_to_session_list(call_id: str, dotted_key: str, *items: Any) -> Dict[str, Any]:
    """Atomically append items to a list field (e.g. "intake.symptoms").

    Use this instead of read_session()+patch_session() when appending, because a
    read-then-write split across two calls races (the read happens outside the
    lock). This does the read, append, and write all under one exclusive lock.
    """
    path = session_path(call_id)
    parts = dotted_key.split(".")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.seek(0)
                raw = f.read().strip()
                current = json.loads(raw) if raw else default_session(call_id)
                if not isinstance(current, dict):
                    current = default_session(call_id)

                node = current
                for key in parts[:-1]:
                    node = node.setdefault(key, {})
                lst = node.setdefault(parts[-1], [])
                if not isinstance(lst, list):
                    lst = []
                lst.extend(items)
                node[parts[-1]] = lst

                f.seek(0)
                f.truncate()
                json.dump(current, f, indent=2)
                f.flush()
                return current
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("append_to_session_list failed for %s: %s", call_id, exc)
        return read_session(call_id)


__all__ = [
    "append_to_session_list",
    "default_session",
    "patch_session",
    "read_session",
    "session_path",
    "SESSIONS_DIR",
]
