"""PATCH illustration for server/agents/execution_agent/tools/registry.py

Register the calendar tools next to gmail and triggers. Apply to the REAL file.
"""

# --- change the import line -----------------------------------------------
from . import gmail, triggers, calendar   # add calendar


# --- get_tool_schemas(): add calendar schemas -----------------------------
def get_tool_schemas():
    return [
        *gmail.get_schemas(),
        *calendar.get_schemas(),    # NEW
        *get_task_schemas(),        # includes triage_screen once tasks/__init__ is patched
        *triggers.get_schemas(),
    ]


# --- get_tool_registry(): add calendar callables --------------------------
def get_tool_registry(agent_name):
    registry = {}
    registry.update(gmail.build_registry(agent_name))
    registry.update(calendar.build_registry(agent_name))   # NEW
    registry.update(get_task_registry(agent_name))
    registry.update(triggers.build_registry(agent_name))
    return registry
