# Voice AI Agent — Medical Office Intake & Scheduling

A voice-first agent for doctor's-office intake, urgency triage, appointment scheduling, and human handoff, built on [OpenPoke](https://github.com/shlokkhemani/openpoke)'s interaction/execution multi-agent stack. A caller speaks to the browser; an **interaction agent** runs the conversation and orchestrates three persistent, tool-scoped **execution agents** (medical, calendar, gmail) that run **in parallel** and coordinate through a shared per-call session file.

## Architecture

```
Browser (Next.js)                         FastAPI backend (server/)
┌──────────────────────┐                  ┌────────────────────────────────────────────┐
│ Voice interface       │  transcript     │ Interaction Agent  (the orchestrator)      │
│ (Web Speech API)      │ ───────────────▶│  tools: send_message_to_agent,             │
│ STT → chat → TTS      │ ◀─────────────  │  record_intake / read_intake,              │
│ barge-in, Alt+Space   │   reply (TTS)   │  request_human_handoff, wait, send_draft   │
└──────────────────────┘                  └───────┬────────────────────────────────────┘
                                                  │ parallel fan-out (asyncio, per turn)
                                     ┌────────────┼──────────────┐
                                     ▼            ▼              ▼
                              medical-agent  calendar-agent  gmail-agent
                              triage_screen  clinic_find_    gmail_create_draft,
                              (rules + LLM   slots, clinic_  gmail_execute_draft, …
                              fallback)      book_slot, …    (Composio Gmail)
                                     │            │              │
                                     └──── reads/writes ─────────┘
                                                  ▼
                              server/data/sessions/<call_id>.json
                              (fcntl-locked "blackboard" — single source of truth)
```

### The agents

| Agent | Role | Tools | Session I/O |
|---|---|---|---|
| **Interaction agent** (`server/agents/interaction_agent/`) | Talks to the caller, one question at a time; owns all orchestration and voice UX | `send_message_to_agent`, `send_message_to_user`, `wait`, `send_draft`, `record_intake`, `read_intake`, `request_human_handoff` | writes `caller`/`intake` fields |
| **`medical-agent`** | Triage screening only — never speaks to the caller | `triage_screen` | reads intake → writes `triage` |
| **`calendar-agent`** | Schedule reads, slot offers, booking (Google Calendar via Composio, with mock fallback) | `calendar_find_events`, `calendar_find_free_slots`, `calendar_create_event`, `clinic_read_schedule`, `clinic_find_slots`, `clinic_book_slot`, … | reads `triage.urgency` → writes `availability`, `booking` |
| **`gmail-agent`** | Prior-context pull; sends the booking confirmation email | `gmail_create_draft`, `gmail_execute_draft`, `gmail_search_people`, … | reads `booking`/`caller.email` → writes `confirmation` |
| **Voice interface** (`web/components/chat/useVoiceInterface.ts`) | *Not an agent* — a browser shim: SpeechRecognition → same `/api/chat` path as typed text → SpeechSynthesis, with barge-in interruption and Alt+Space push-to-talk | — | — |

Roles are resolved from agent names in `execution_agent/roles.py` (exact match, then keyword fallback, e.g. "scheduler" → calendar). The three specialized agents are deliberately **pure leaf workers**: only the `general` role gets the inter-agent `message_agent` tool, so agents never delegate to each other — the interaction agent owns every cross-agent step. Keeping the roster to three persistent roles (not per-caller spawns) is the answer to the execution-agent-overload problem: a tiny roster, scoped toolsets, and one `triage_screen` task instead of a wall of endpoints.

### How they interact

**Dispatch & fan-in (unchanged OpenPoke core).** In a single turn the interaction agent may emit multiple `send_message_to_agent` calls; each becomes a non-blocking `asyncio` task (`interaction_agent/tools.py`). `ExecutionBatchManager` (`batch_manager.py`) registers them in one batch — each runs its own `ExecutionAgentRuntime` tool loop (90 s timeout). When the batch drains, results are combined into `[SUCCESS]/[FAILED] <agent>: …` and fed back to the interaction agent, which replies to the caller and may dispatch the next batch. **Design rule: independent work goes in the same turn (parallel); dependent work waits for the batch to return.**

**Coordination via blackboard, not prompts.** Agents don't pass data through each other's prompt text. All state lives in `server/data/sessions/<call_id>.json` (`services/session/store.py`, fcntl `LOCK_EX` — same pattern as OpenPoke's `roster.json`):

```jsonc
{
  "call_id": "...",
  "status": "intake",   // intake | emergency | scheduling | booked | handoff
  "caller":  { "name", "dob", "callback", "email", "is_new" },
  "intake":  { "symptoms": [], "insurance", "notes" },
  "triage":  { "level", "urgency", "confidence", "rationale",
               "source", "matched_rules", "decision_path", "needs_human_review" },
  "availability": [], "booking": {}, "confirmation": {}, "handoff": {}
}
```

This decouples the agents (calendar reads `triage.urgency`, writes `availability`; gmail reads `booking`), makes writes concurrency-safe, and leaves an auditable record of every call. `record_intake` lives on the *interaction* agent on purpose — persisting a field should never cost an LLM round-trip.

### Call flow (phases)

1. **Connect (parallel fan-out).** In one turn: `calendar-agent` reads today's schedule ∥ `gmail-agent` pulls prior caller context — hidden behind the greeting.
2. **Intake with continuous background triage.** After *every* substantive answer: `record_intake`, then re-fire `medical-agent` non-blockingly. An emergency ("crushing chest pain") is caught mid-intake, not after six questions. Triage only surfaces to the caller when it returns emergency.
3. **911 gate (synchronization barrier).** On `level == "emergency"`: caller is told to call 911, `status` flips to `"emergency"`, and booking stops.
4. **Schedule (speculative pre-fetch).** Once urgency is set: `clinic_find_slots(urgency)` is dispatched *in the same turn* as the final confirmation questions (callback number, email) — slots are ready when the caller finishes answering. Slot windows match priority: `same_day` → today, `soon` → 2–3 days, `routine` → next available.
5. **Book → confirm (sequential).** Caller picks a slot → `clinic_book_slot` writes `booking` → interaction agent verifies success **from the session file** (`booking.event_id` + `status == "booked"`, not agent prose, which a timeout can truncate) → `gmail-agent` sends the confirmation email → the summary is read back.

### Triage: explainable and conservative

`tasks/clinic/` decides priority in strict order:

1. **Rule-based decision tree** (`rules.py`) — deterministic, zero-latency, keyword red flags with a negation guard ("denies chest pain" doesn't fire). Levels: `emergency` (urgency 5) / `same_day` (4) / `soon` (3) / `routine` (2). Every verdict carries `matched_rules` and `decision_path`.
2. **Model fallback** — only when symptoms match no rule, an LLM screen runs via the existing OpenRouter client (`source: "model_fallback"`).
3. **Conservative default** — if the model fails: `soon` + `needs_human_review: true`.

Ambiguity escalates one level up, never down, and modifiers can never *create* an emergency — only explicit red flags can. Sensitive situations (abuse, grief, mental health) set `needs_human_review`, prompting a human-handoff offer. The agent never diagnoses, prescribes, or claims clinical certainty — it supports intake and routing only.

### Safety: defense in depth

- **911 gate is enforced in code, not just prompt.** `calendar_create_event` / `clinic_book_slot` take `call_id` and refuse (`tools/calendar.py:_emergency_block`) whenever session `status` is `"emergency"` or `"handoff"` — even if the model tries anyway.
- **Human handoff at any point** via `request_human_handoff(call_id, reason, details)` on the interaction agent (no execution round-trip). Reasons: `caller_request`, `uncertainty`, `sensitive`, `tool_failure`, `emergency_support`. After handoff, booking is blocked and the human owns the call. (Transfer itself is mocked: session write + log.)
- **Idempotent, resilient booking** with a graceful mock fallback (`_MOCK_SLOTS`, `mock_evt_*` ids) so a Composio outage can't sink a call.
- **Critical details confirmed before acting**: identity, callback number, email, and slot are required in the session before booking proceeds.

### Voice layer

Pure browser, no extra services: SpeechRecognition (STT) → existing `/api/chat` → SpeechSynthesis (TTS). Mic pauses while the agent speaks and resumes after, with a 600 ms post-TTS cooldown so the agent doesn't transcribe its own echo. Two modes: click-to-toggle continuous listening, or Alt/Option+Space hold-to-talk. Starting to talk (or holding) interrupts in-progress TTS — barge-in. All reasoning stays server-side in the interaction agent.

## Repo layout

```
server/
  agents/
    interaction_agent/     # orchestrator: runtime, tools.py, intake_tools.py, system_prompt.md
    execution_agent/       # runtime, batch_manager.py, roles.py
      tools/               # gmail.py, calendar.py (incl. clinic_* + 911 gate), registry.py
      tasks/clinic/        # triage_screen: rules.py (decision tree), tool.py, schemas.py
      tasks/search_email/  # existing OpenPoke email search task
  services/
    session/               # store.py (fcntl blackboard), active_call.py (call_id lifecycle)
    calendar/              # Composio Google Calendar client (mirrors gmail/)
    gmail/  triggers/  conversation/   # existing OpenPoke services (reused untouched)
  routes/                  # chat.py, calendar.py, gmail.py, meta.py
web/                       # Next.js chat UI + voice interface hook
tests/test_triage_and_handoff.py   # rule decision tree, triage integration, handoff gates
```

## Quickstart

```bash
cp .env.example .env       # add OPENROUTER_API_KEY (required); COMPOSIO_* for real Gmail/Calendar
cd server && pip install -r requirements.txt && python server.py
cd web && npm install && npm run dev    # http://localhost:3000 — click the mic
```

Gmail and Google Calendar connect via Composio from the settings modal; without them, calendar tools fall back to mock slots/bookings, so the full flow demos offline.

## Key trade-offs & what I'd improve

- **Single-call state**: the conversation log and `ExecutionBatchManager` batch state are global — fine for one caller, would need keying by `call_id` for multi-tenant.
- **Background-triage cost**: re-screening every turn adds LLM calls; mitigated by the zero-cost rule tree first pass (a cheaper triage model is a config change away).
- **Privacy**: the session file holds PHI-like data; a real deployment needs encryption at rest and retention limits.
- **Triage fidelity**: keyword red flags + LLM is conservative but not clinical — the design errs toward escalation and makes the 911/handoff gate impossible to bypass in code.
- **Voice**: Web Speech API is demo-grade; production would use streaming STT/TTS for latency and proper endpointing.
