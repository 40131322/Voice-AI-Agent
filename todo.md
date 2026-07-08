# TODO — Parallel Multi-Agent Medical Intake on OpenPoke

Build a voice-driven medical-office intake + scheduling system on top of the existing
OpenPoke codebase (`server/` FastAPI + `web/` Next.js). Five persistent agents coordinate
through a shared fcntl-locked session file ("blackboard"), running in parallel on OpenPoke's
existing async orchestration.

**Golden rule (from the design doc):** you are *not* writing an orchestrator — OpenPoke already
runs `send_message_to_agent` calls concurrently. Independent work goes in the *same* interaction
turn (true parallelism); dependent work goes in the *next* turn (after the batch drains).

**Net new code:** one session store, one triage task, one calendar module (mirror of Gmail).
Everything else is prompt/config edits + reuse.

---

## Phase 0 — Setup & baseline (get the stock app running first)

- [ ] Clone/enter repo; `cp .env.example .env`.
- [ ] Add keys to `.env`: `OPENROUTER_API_KEY`, `COMPOSIO_API_KEY`, `COMPOSIO_GMAIL_AUTH_CONFIG_ID`.
- [ ] `python3.10 -m venv .venv && source .venv/bin/activate`
- [ ] `pip install -r server/requirements.txt`
- [ ] `npm install --prefix web`
- [ ] Run backend: `python -m server.server --reload`  (port 8001)
- [ ] Run frontend: `npm run dev --prefix web`  (localhost:3000)
- [ ] Connect Gmail via Settings → Gmail (Composio OAuth). Confirm a Gmail draft works end-to-end.
- [ ] **Checkpoint:** stock OpenPoke chat + Gmail tooling works before adding anything.

**Verify:** send a chat message, watch the interaction agent dispatch an execution agent, and
confirm the combined `[SUCCESS] <agent>: ...` payload comes back.

---

## Phase 1 — Session-file "blackboard" store (foundation, do this first)

The single source of truth every agent reads/writes. Copy the proven fcntl lock pattern from
`server/services/execution/roster.py`.

- [ ] **New:** `server/services/session/__init__.py`
- [ ] **New:** `server/services/session/store.py`
  - Copy the `fcntl.LOCK_EX | LOCK_NB` + retry/backoff logic from `roster.py` (`save()`).
  - Path: `server/data/sessions/<call_id>.json` (mkdir parents; `server/data/` is git-ignored).
  - `read_session(call_id) -> dict` — return default skeleton if file absent.
  - `patch_session(call_id, partial: dict) -> dict` — read-modify-write under lock, deep-merge.
  - Session skeleton (from design doc §Coordination):
    ```json
    {
      "call_id": "c_8f3a",
      "status": "intake",
      "caller":  { "name": null, "dob": null, "callback": null, "is_new": null },
      "intake":  { "symptoms": [], "insurance": null, "notes": null, "updated_at": null },
      "triage":  { "level": null, "urgency": null, "confidence": null, "rationale": null },
      "context": { "prior_visits": [], "existing_events": [] },
      "availability": [],
      "booking": { "slot_id": null, "event_id": null, "confirmation_id": null },
      "confirmation": { "email_status": null, "message_id": null }
    }
    ```
- [ ] **New intake tool** so writing a field costs no LLM call (Intake Record = tool, not agent):
  - `record_intake(call_id, patch)` and `read_intake(call_id)` callables.
  - Decide surface: expose as an execution-agent tool module (new
    `server/agents/execution_agent/tools/intake.py`) and/or an interaction-agent tool in
    `server/agents/interaction_agent/tools.py`. The interaction agent needs to write caller
    answers directly, so an interaction-agent tool is the pragmatic choice.
- [ ] **Verify:** unit-test concurrent `patch_session` from two threads/processes — no lost writes,
      no corruption (this is the load-bearing concurrency claim).

---

## Phase 2 — Triage task (Medical Agent brain) + 911 gate

`triage_screen` is a **task** (calls OpenRouter/Anthropic directly), NOT a Composio tool —
mirror `tasks/search_email/` which calls the model internally.

- [ ] **New dir:** `server/agents/execution_agent/tasks/clinic/`
  - `__init__.py`
  - `schemas.py` — tool schema `triage_screen(call_id)` (or accept the session blob); Pydantic
    result model `{ level: emergency|urgent|routine, urgency: int, confidence: float, rationale: str }`.
  - `system_prompt.py` — pure clinical-screening prompt, **no personality**. Rules: never diagnose,
    never prescribe, never claim clinical certainty; err toward escalation; flag red-flag symptoms
    (chest pain, stroke signs, difficulty breathing, etc.) as `emergency`.
  - `tool.py` — read session file → call `request_chat_completion` (from `server.openrouter_client`)
    → write `triage` block back via `patch_session`. Model from `execution_agent/tasks/search_email/tool.py`.
- [ ] **Register the task:** edit `server/agents/execution_agent/tasks/__init__.py` to include
      clinic schemas in `get_task_schemas()` and callables in `get_task_registry()` (mirror the
      `search_email` wiring already there).
- [ ] **(Optional hard isolation):** scope the tool registry in
      `server/agents/execution_agent/tools/registry.py` so only the "Medical Agent" is handed the
      `triage_screen` schema — enforces that the Voice/Interaction agent never triages itself.
- [ ] **Cheaper model on hot path:** add `triage_model` (e.g. a smaller Anthropic id) to
      `server/config.py` `Settings` and point the triage task at it. Keep Sonnet for the voice agent.
      (Note: current config models are `anthropic/claude-sonnet-4`.)
- [ ] **911 code gate (defense in depth):** when `status == "emergency"`, the calendar
      `create_event` tool (Phase 4) must **refuse** to book. Prompt rule + code guard both.
- [ ] **Verify:** feed a "crushing chest pain" transcript → `level == emergency`; feed a routine
      cold → `level == routine`. Confirm booking is blocked while status is emergency.

---

## Phase 3 — Calendar module (the one new integration; mirror Gmail 1:1)

Copy the Gmail modules exactly (same Composio client pattern, `_execute` + `get_active_*_user_id`).

- [ ] **New:** `server/services/calendar/__init__.py` + `server/services/calendar/client.py`
  - Copy `server/services/gmail/client.py`; expose `execute_calendar_tool` and
    `get_active_calendar_user_id` (+ connect/disconnect/status mirroring Gmail).
- [ ] **New:** `server/agents/execution_agent/tools/calendar.py`
  - Copy `server/agents/execution_agent/tools/gmail.py`; wrap Composio Google Calendar actions
    (confirm exact slugs in the Composio dashboard — they change):
    - read schedule → `GOOGLECALENDAR_FIND_EVENT`
    - availability → `GOOGLECALENDAR_FIND_FREE_SLOTS` (or a free/busy query)
    - booking → `GOOGLECALENDAR_CREATE_EVENT`
  - `find_free_slots(urgency)` writes `availability`; `create_event(...)` writes `booking`.
  - **Idempotent booking:** key the event on `call_id + slot_id` so a retry never double-books.
  - **911 guard** inside `create_event`: refuse when session `status == "emergency"`.
- [ ] **Register:** add `calendar` to `server/agents/execution_agent/tools/registry.py` next to
      `gmail` (both `get_tool_schemas()` and `get_tool_registry()`).
- [ ] **Config/auth:** add `composio_calendar_auth_config_id` to `server/config.py` +
      `COMPOSIO_CALENDAR_AUTH_CONFIG_ID` to `.env.example`. Add a Calendar OAuth connect **backend
      route** mirroring `server/routes/gmail.py` (reuse existing Settings UI — no new JS).
- [ ] **Mock fallback:** every calendar tool returns canned slots/booking if Composio isn't set up,
      so the demo can't be sunk by a Composio outage. The concurrency design is identical either way.
- [ ] **Verify:** `find_free_slots` returns slots; `create_event` writes a `booking` with an
      `event_id`; second identical call does not create a duplicate.

---

## Phase 4 — Interaction-agent prompt: teach the parallelism

The model decides batching, so the parallel behavior lives in the prompt.
Edit `server/agents/interaction_agent/system_prompt.md`.

- [ ] Add the medical-intake persona (warm receptionist; one question at a time; never diagnose/
      prescribe/claim certainty) — **but** keep clinical reasoning out of the interaction agent.
- [ ] Add the dispatch rules (verbatim intent from design doc §Interaction-Agent Prompt Rules):
  - "At call start, in a **single** message, ask the Calendar Agent to read today's schedule
    **and** the Gmail Agent to pull prior context. Don't wait — greet the caller meanwhile."
  - "After each caller answer, silently update the record and ask the Medical Agent to re-screen.
    Only interrupt the caller if it returns an emergency."
  - "On emergency: tell the caller to call 911 immediately and do not book anything."
  - "When urgency is set, ask the Calendar Agent for free slots **in the same message** you ask the
    caller your last confirmation question." (speculative pre-fetch)
  - "Only after the caller picks a slot, book it; only after booking succeeds, send confirmation email."
  - "Prefer one existing agent per role; never create a second Calendar/Gmail/Medical agent."
- [ ] Keep execution-agent prompts (`execution_agent/system_prompt.md`) as pure task machines
      (no personality) — interaction agent owns voice/UX.
- [ ] **Verify:** in a single caller turn, confirm two `send_message_to_agent` calls fire in the
      same batch at connect (Calendar ∥ Gmail); confirm triage is dispatched non-blockingly after
      each substantive answer.

---

## Phase 5 — Phase-by-phase flow wiring & scenarios

Map the dependency graph (design doc §Phase-by-Phase). Parallel edges: connect reads, and
free-slot lookup ∥ final questions. Everything crossing the 911 barrier is sequenced.

- [ ] **Connect (parallel fan-out):** Calendar reads schedule ∥ Gmail pulls prior context, hidden
      behind the greeting. Results land in `context.existing_events` / `context.prior_visits`.
- [ ] **Intake + continuous background triage (the key win):** voice asks → `record_intake` →
      dispatch Medical Agent non-blockingly each turn. Surface triage to caller only when
      `level == emergency` or intake complete (reuse the `wait` discipline to avoid noise).
- [ ] **911 barrier:** on emergency → speak 911 instruction, set `status = "emergency"`, no booking
      agent dispatched, code gate active.
- [ ] **Schedule (speculative pre-fetch):** once `triage.urgency` set, dispatch `find_free_slots` in
      the SAME turn as the voice agent's final confirmation questions (insurance, callback #).
- [ ] **Book → confirm (sequential, minimal):** caller picks slot → `create_event` writes `booking`
      → dispatch Gmail Agent to send confirmation (reads `booking`) → voice reads back summary.
- [ ] **(Optional) callback reminder:** reuse existing Triggers system (`server/services/triggers/`).
- [ ] **Three demo scenarios** (per brief): (1) routine booking end-to-end; (2) mid-intake emergency
      → 911, no booking; (3) urgent-but-not-911 → prioritized slot.

---

## Phase 6 — Voice INTERFACE (add last; text-first MVP)

Verdict: yes, add voice as an MVP/demo layer — but call it a **Voice Interface, not a
Voice Agent**. OpenPoke ships no native voice, so the "voice" piece is just speech I/O
bolted onto the existing Interaction Agent (Claude via OpenRouter, unchanged). It is NOT
a new reasoning agent and must NOT enter the roster.

**Build order (deliberate — never debug voice and orchestration at once):**

```text
Text-first MVP  →  prove the agent graph  →  add browser STT/TTS last
```

- [ ] **MVP: text stand-in** — prove the whole agent graph works end-to-end by typing into the
      existing chat UI. Do this before touching audio.
- [ ] **Then: browser Web Speech API** (~20 lines, no key, no model downloads, runs in the existing
      Next.js chat UI): `SpeechRecognition` (STT, continuous) + `SpeechSynthesisUtterance` (TTS)
      in `web/components/chat/`. Reference pattern: the healthcare app's `src/context/UserContext.jsx`
      (continuous recognition + speak-with-mic-pause) — copy it.

**CRITICAL — the Voice Interface is a thin client shim, not an agent.**
It has **no tools** and **no roster entry**. Do NOT give it calendar tools, gmail tools, triage
tools, or delegation/`send_message_to_agent`. Giving the voice layer tools re-creates exactly the
"execution-agent overload" the design fights against.

Its entire job is the six-step client loop:

```text
1. speech-to-text (mic → transcript)
2. send transcript to the Interaction Agent (same /chat path text uses)
3. receive the Interaction Agent's text response
4. text-to-speech (response → speaker)
5. pause mic while speaking
6. resume mic after speaking
```

Clean architecture (keep it this literal):

```text
Browser mic → STT transcript → Interaction Agent → text response → TTS speaker
```

NOT:

```text
Voice Agent → triage / calendar / gmail        ← wrong: no such agent, no tools on the voice layer
```

- [ ] **(Alt, harder) offline Python:** `vosk` (STT) + `pyttsx3` (TTS) + `sounddevice` — no API, but
      needs model downloads + native audio bindings (install pain, esp. macOS). Skip unless required.
- [ ] Ruled out by "no extra API" constraint: Whisper API, Deepgram, ElevenLabs, Twilio.
- [ ] **Verify:** speak "I have a bad cough" → transcript reaches the Interaction Agent → spoken
      reply plays. Confirm the roster still shows only Calendar/Medical/Gmail — no "voice" agent.

---

## Phase 7 — Verification, docs, and trade-offs writeup

- [ ] Concurrency test: two `send_message_to_agent` in one turn → two concurrent asyncio tasks →
      batch drains → single combined `[SUCCESS]` fan-in (confirm against `batch_manager.py`:
      90s timeout, ≤8 tool iterations per agent).
- [ ] Safety test: 911 gate cannot be bypassed (prompt AND code). Booking blocked under emergency.
- [ ] Idempotency test: duplicate booking attempt does not double-book.
- [ ] Update `README.md` with the medical-intake setup, the three scenarios, and the optimization
      trade-offs list below.
- [ ] **Optimizations to document (README trade-offs):** fan-out independent reads at connect;
      continuous background triage → early 911; shared fcntl session file; speculative slot
      pre-fetch; persistent few agents (tiny roster); tasks over tool-walls; cheaper triage model;
      defense-in-depth 911 gate; idempotent booking.
- [ ] **Risks to state:** single global `_batch_state` in `ExecutionBatchManager` interleaves across
      concurrent *callers* (fine for single-call demo; key batches by `call_id` for multi-tenant);
      background-triage cost (mitigate: cheaper model, re-screen only on substantive answers);
      Composio latency (hidden by pre-fetch + mock fallback); session file holds PHI (needs
      encryption-at-rest + retention limits in a real system); 911 screening is conservative but not
      clinical — always err toward escalation.

---

## Scope tiers (cut order if short on time)

- **MVP (demoable):** session store · text intake · triage task + 911 gate · urgency · Calendar
  Agent with **mock** slots+booking · real Gmail confirmation · 3 scenarios · README.
- **Full (if time):** real Composio Google Calendar · connect-phase Gmail context pull · callback
  Triggers · optional offline voice.
- **Cut first (in order):** connect-phase context pull → callback triggers → real Calendar (keep
  mock) → conflict handling. **Never cut the 911 gate or the confirmation step.**

---

## File-level map (quick reference)

| Type | Files |
|---|---|
| **Reuse untouched** | interaction/execution runtimes, `batch_manager.py`, `services/execution/roster.py`, conversation memory + summarization, `services/gmail/*`, `tools/gmail.py`, `services/triggers/*`, `openrouter_client/*`, existing chat UI, `routes/chat.py` |
| **Edit (prompt/config)** | `interaction_agent/system_prompt.md` (persona + parallel dispatch); `execution_agent/system_prompt.md` (role instructions); `tasks/__init__.py` (register triage); `tools/registry.py` (register calendar); `config.py` (calendar auth id + `triage_model`) |
| **New — session store** | `server/services/session/store.py` (fcntl JSON, copy of roster) + `record_intake`/`read_intake` tool |
| **New — triage task** | `server/agents/execution_agent/tasks/clinic/{schemas,system_prompt,tool}.py` (`triage_screen`, OpenRouter) |
| **New — calendar** | `server/services/calendar/client.py` + `server/agents/execution_agent/tools/calendar.py` (mirror Gmail) + calendar route in `routes/` |
| **Optional new** | `escalate_to_human` schema in `interaction_agent/tools.py`; offline voice wrapper; Web Speech layer in `web/components/chat/` |

**Only network calls:** OpenRouter (reasoning) + Composio (Gmail/Calendar).
