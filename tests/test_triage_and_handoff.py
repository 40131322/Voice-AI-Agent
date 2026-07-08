"""Tests for the rule-based triage decision tree + human handoff path.

Runs WITHOUT the full server stack: heavy modules (fastapi app, composio,
openrouter) are stubbed so the pure decision logic is what's under test.

Run from the repo root:  python3 tests/test_triage_and_handoff.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
PASS = FAIL = 0


def check(name: str, cond: bool, detail: Any = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {detail}")


def load(mod_name: str, rel_path: str, pkg: bool = False):
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(
        mod_name, path, submodule_search_locations=[str(path.parent)] if pkg else None
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def dummy_pkg(name: str, path: Optional[str] = None):
    mod = types.ModuleType(name)
    mod.__path__ = [path] if path else []
    sys.modules[name] = mod
    return mod


# ---------------------------------------------------------------------------
# Stub the heavy parts of the `server` package, load the real light parts.
# ---------------------------------------------------------------------------
dummy_pkg("server", str(ROOT / "server"))
load("server.logging_config", "server/logging_config.py")
dummy_pkg("server.services")
store = load("server.services.session.store", "server/services/session/store.py")
session_pkg = dummy_pkg("server.services.session")
for fn in ("read_session", "patch_session", "append_to_session_list", "default_session", "session_path"):
    setattr(session_pkg, fn, getattr(store, fn))

# server.config stub: no API key => triage model fallback is unavailable.
config_stub = types.ModuleType("server.config")
config_stub.get_settings = lambda: types.SimpleNamespace(
    openrouter_api_key=None,
    execution_agent_model="stub-model",
    handoff_staff_line="front-desk staff line (mock transfer)",
)
sys.modules["server.config"] = config_stub

# server.openrouter_client stub (must not be called in these tests).
orc = types.ModuleType("server.openrouter_client")
async def _no_call(**_: Any):  # pragma: no cover
    raise AssertionError("model should not be called")
orc.request_chat_completion = _no_call
sys.modules["server.openrouter_client"] = orc

# server.services.execution stub log store.
exec_stub = types.ModuleType("server.services.execution")
class _Logs:
    def record_action(self, *_a: Any, **_k: Any) -> None:
        pass
exec_stub.get_execution_agent_logs = lambda: _Logs()
sys.modules["server.services.execution"] = exec_stub

# Clinic task package (real files).
dummy_pkg("server.agents")
dummy_pkg("server.agents.execution_agent")
dummy_pkg("server.agents.execution_agent.tasks")
dummy_pkg("server.agents.execution_agent.tasks.clinic",
          str(ROOT / "server/agents/execution_agent/tasks/clinic"))
rules = load("server.agents.execution_agent.tasks.clinic.rules",
             "server/agents/execution_agent/tasks/clinic/rules.py")
load("server.agents.execution_agent.tasks.clinic.schemas",
     "server/agents/execution_agent/tasks/clinic/schemas.py")
load("server.agents.execution_agent.tasks.clinic.system_prompt",
     "server/agents/execution_agent/tasks/clinic/system_prompt.py")
tool = load("server.agents.execution_agent.tasks.clinic.tool",
            "server/agents/execution_agent/tasks/clinic/tool.py")

# Interaction-agent package with a stub tools.ToolResult (real one drags in
# the batch manager / conversation stack).
dummy_pkg("server.agents.interaction_agent", str(ROOT / "server/agents/interaction_agent"))
tools_stub = types.ModuleType("server.agents.interaction_agent.tools")
@dataclass
class ToolResult:
    success: bool
    payload: Any = None
    user_message: Optional[str] = None
    recorded_reply: bool = False
tools_stub.ToolResult = ToolResult
sys.modules["server.agents.interaction_agent.tools"] = tools_stub
intake_tools = load("server.agents.interaction_agent.intake_tools",
                    "server/agents/interaction_agent/intake_tools.py")

evaluate_rules = rules.evaluate_rules
conservative_default = rules.conservative_default


# ---------------------------------------------------------------------------
# 1. Decision tree unit tests
# ---------------------------------------------------------------------------
print("\n[1] decision tree")

d = evaluate_rules(["crushing chest pain"])
check("chest pain -> emergency u5", d.level == "emergency" and d.urgency == 5, d)
check("emergency has red flags + rule ids", d.red_flags and "er-chest-pain" in d.matched_rules, d)

d = evaluate_rules(["no chest pain", "mild cold"])
check("negation: 'no chest pain' not emergency", d.level == "routine", d)

d = evaluate_rules(["high fever", "getting worse"])
check("high fever -> same_day u4", d.level == "same_day" and d.urgency == 4, d)

d = evaluate_rules(["sore throat"])
check("sore throat -> soon u3", d.level == "soon" and d.urgency == 3, d)

d = evaluate_rules(["sore throat"], notes="had it for a week, won't go away")
check("soon + duration modifier -> same_day", d.level == "same_day", d)

d = evaluate_rules(["medication refill"])
check("refill -> routine u2", d.level == "routine" and d.urgency == 2, d)

d = evaluate_rules(["runny nose"], notes="caller is pregnant")
check("routine + high-risk modifier -> soon", d.level == "soon", d)

d = evaluate_rules([])
check("empty intake -> routine u1 low conf", d.level == "routine" and d.urgency == 1 and d.confidence <= 0.3, d)

d = evaluate_rules(["strange tingling aura in my elbow"])
check("unmatched -> no level + human review", d.level is None and d.needs_human_review, d)
d = conservative_default(d)
check("conservative default -> soon + review", d.level == "soon" and d.needs_human_review, d)

d = evaluate_rules(["back pain"], notes="I was assaulted last night")
check("sensitive keyword -> needs_human_review", d.level == "soon" and d.needs_human_review, d)

d = evaluate_rules(["fever", "trouble breathing"])
check("mixed symptoms: emergency wins", d.level == "emergency", d)
check("decision path recorded", len(d.decision_path) >= 2, d.decision_path)


# ---------------------------------------------------------------------------
# 2. triage_screen integration (rules primary; conservative default w/o key)
# ---------------------------------------------------------------------------
print("\n[2] triage_screen task")
store.SESSIONS_DIR = Path("/tmp/vaa-test-sessions")

def seed(call_id: str, symptoms, notes=None):
    store.patch_session(call_id, {"intake": {"symptoms": symptoms, "notes": notes}})

seed("t1", ["crushing chest pain"])
r = asyncio.run(tool.triage_screen("t1"))
s = store.read_session("t1")
check("emergency verdict via rules", r["triage"]["level"] == "emergency" and r["triage"]["source"] == "rules", r)
check("session status flipped to emergency", s["status"] == "emergency", s["status"])

seed("t2", ["annual physical"])
r = asyncio.run(tool.triage_screen("t2"))
check("routine verdict, explainable", r["triage"]["level"] == "routine" and r["triage"]["matched_rules"], r)

seed("t3", ["mysterious glowing rash pattern zzz"])
r = asyncio.run(tool.triage_screen("t3"))
check("unmatched + no model -> conservative default",
      r["triage"]["level"] == "soon" and r["triage"]["source"] == "default_conservative"
      and r["triage"]["needs_human_review"], r)


# ---------------------------------------------------------------------------
# 3. Human handoff path
# ---------------------------------------------------------------------------
print("\n[3] human handoff")
store.patch_session("h1", {"caller": {"name": "Ana", "callback": "555-0100"}})
res = intake_tools.request_human_handoff("h1", "caller_request", "wants to discuss billing")
s = store.read_session("h1")
check("handoff tool succeeds", res.success, res)
check("status -> handoff", s["status"] == "handoff", s["status"])
check("handoff block recorded", s["handoff"]["requested"] and s["handoff"]["reason"] == "caller_request", s["handoff"])
check("callback surfaced to staff", s["handoff"]["callback_on_file"] == "555-0100", s["handoff"])

# Emergency status must survive a handoff.
store.patch_session("h2", {"status": "emergency"})
intake_tools.request_human_handoff("h2", "emergency_support")
check("emergency status wins over handoff", store.read_session("h2")["status"] == "emergency")

# Booking guard: mirror of the check in tools/calendar.py (module drags in
# composio, so replicate the guard condition against the real session).
check("booking blocked while handoff", store.read_session("h1")["status"] in ("emergency", "handoff"))


print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
