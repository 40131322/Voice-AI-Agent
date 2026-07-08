"""PATCH illustration for server/agents/execution_agent/tasks/__init__.py

Add the clinic triage task alongside the existing search_email task. Apply these
edits to the REAL file (do not overwrite it — it already registers search_email).
"""

# --- add near the existing search_email imports ---------------------------
from .clinic import get_schemas as _get_clinic_schemas
from .clinic import build_registry as _build_clinic_registry


# --- get_task_schemas(): add clinic schemas -------------------------------
def get_task_schemas():
    return [
        *_get_email_search_schemas(),   # existing
        *_get_clinic_schemas(),         # NEW
    ]


# --- get_task_registry(): add clinic callables ----------------------------
def get_task_registry(agent_name):
    registry = {}
    registry.update(_build_email_search_registry(agent_name))  # existing
    registry.update(_build_clinic_registry(agent_name))        # NEW
    return registry
