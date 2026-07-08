# Scaffold — Parallel Multi-Agent Medical Intake

Skeleton files for the build described in `todo.md`. They mirror the real OpenPoke
`server/` tree so you can copy them straight into the repo. Every stub follows a
pattern that already exists in the codebase, and `TODO(...)` comments mark the exact
spots to fill in.

## What's here

```
server/
  services/session/__init__.py           # blackboard exports
  services/session/store.py              # ✅ WORKING fcntl read/patch (copy of roster lock pattern)
  services/calendar/__init__.py          # calendar service exports
  services/calendar/client.py            # STUB — copy body from services/gmail/client.py
  agents/execution_agent/tasks/clinic/
    __init__.py                          # task package
    schemas.py                           # ✅ triage_screen schema + Pydantic result
    system_prompt.py                     # ✅ clinical screener prompt (no personality)
    tool.py                              # triage impl — wire request_chat_completion signature
  agents/execution_agent/tools/calendar.py   # ✅ schemas + handlers, 911 guard + idempotent booking (mock on)
  agents/interaction_agent/intake_tools.py   # ✅ record_intake / read_intake (lives on interaction agent)
  agents/execution_agent/tasks/__init__.PATCH.py    # how to register the triage task
  agents/execution_agent/tools/registry.PATCH.py    # how to register calendar tools
```

`✅` = usable as-is (or after a one-line import path check). `STUB` = copy the named
Gmail file and rename. `.PATCH.py` files are illustrations — apply the shown edits to
the real files; do NOT overwrite the originals.

## `record_intake` lives on the interaction agent — as requested

It's in `agents/interaction_agent/intake_tools.py`. The interaction agent is the one
receiving each caller answer, and persisting a field must not cost an LLM round-trip.
The execution agents (Medical, Calendar, Gmail) only *read* the same file via
`server.services.session`. Wiring steps are in the bottom of that file.

## Install order (matches todo.md phases)

1. Drop in `services/session/` — run the concurrency test first; everything depends on it.
2. Add `intake_tools.py` and wire into `interaction_agent/tools.py` (3 edits, see file).
3. Add `tasks/clinic/` + apply `tasks/__init__.PATCH.py`.
4. Add `services/calendar/` (copy Gmail) + `tools/calendar.py` + apply `registry.PATCH.py`.
5. Edit `interaction_agent/system_prompt.md` with the dispatch rules (todo.md Phase 4).
6. `config.py`: add `triage_model` and `composio_calendar_auth_config_id`.

## Left intentionally open (need your input / real endpoints)

- **`call_id` source** — how the interaction agent gets a stable id per call (todo.md Phase 1).
- **`request_chat_completion` signature** in `tasks/clinic/tool.py` — match `tasks/search_email/tool.py`.
- **Composio Calendar action slugs** — confirm in your dashboard (they drift).
- **Calendar OAuth route** — mirror `routes/gmail.py`; not scaffolded (backend route only).

`tools/calendar.py` ships with `USE_MOCK = True` so the whole flow is demoable before
Composio Calendar is connected.
