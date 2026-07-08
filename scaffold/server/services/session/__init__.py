"""Session-file blackboard service.

The single source of truth every agent reads/writes during a call. Backed by a
per-call JSON file guarded with fcntl locks (same pattern as
``server/services/execution/roster.py``).
"""

from .store import (
    append_to_session_list,
    default_session,
    patch_session,
    read_session,
    session_path,
)

__all__ = [
    "append_to_session_list",
    "default_session",
    "patch_session",
    "read_session",
    "session_path",
]
