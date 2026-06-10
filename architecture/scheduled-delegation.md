# Scheduled Delegation & Per-Agent Brains — the recurring-work layer (SOP)

> A.N.T. golden rule: if this logic changes, update this SOP **before** the code.
> Builds on [agents-delegation-skills.md]. Lets the owner (in chat) ask the lead to set up a
> **recurring delegated job** that a specialist runs on a schedule, on its own model (brain), with
> results landing on the Delegation board. Moves expensive multi-agent work **off** the interactive
> chat path onto a cadence, where token cost/latency don't matter.
>
> Code: `execution/core/agents.py` (brain sidecar), `execution/core/router.py` (`catalog_models`),
> `execution/core/tasks.py` (recurrence schema + lifecycle), `execution/core/scheduler.py` (the tick),
> `execution/skills/schedule_task.py` (lead-facing, gated), wiring in `execution/runtime.py` +
> `execution/web/server.py`, routes in `execution/web/api.py`, UI in `dashboard/index.html`.

## Why this shape (the token argument)
Live in-chat multi-agent delegation costs 3–5× tokens (each sub-agent reloads its own system prompt,
SOUL, tool schemas, memory; plus brief + report-back round-trips) **while the human waits**. For a
read-only Q&A assistant that's waste — one agent calling two tools is better. But the *same* capability
is sensible **unattended on a schedule**: a 7am drift-check doesn't care about latency, and per-run
token cost is trivial. So recurring delegation, not live delegation, is where specialists earn their keep.

## Per-agent brains (the model dial)
Each profile may pin a **brain** = a DTM router model id (`ollama:qwen3.5:27b`,
`anthropic:claude-opus-4-8`). Stored DTM-native in a one-line sidecar `(<profile dir>)/brain`, NOT in
the giant legacy Hermes `config.yaml`. Resolution: sidecar first, else the legacy config (display only),
else the local default.
- `agents.get_brain_model(name)` → model id or None. `agents.set_brain(name, model_id)` writes/clears.
- Validation is against `router.catalog_models()` (the FULL catalog — Claude ids are valid even before
  the API key is set, so brains can be pre-assigned and "go live" the moment the key lands).
- **Routing:** the `Dispatcher` resolves the assigned profile's brain and passes it to the agent loop;
  a cloud brain flips `ctx.allow_cloud=True` for that run only (still local-first by default, Rule #5).
  The **chat** model dropdown is the lead's brain selector for interactive turns (unchanged).
- Precedence: explicit per-turn model → the agent's brain → local default.

## Recurrence (the schedule)
A recurring task is one board task that re-fires on a cadence (its `task_runs` rows are the history).
New `tasks` columns: `recurring` (0/1), `schedule_spec` (text), `next_run_at` (ms), `paused` (0/1).
Schedule specs (parsed in `scheduler.py`, server local time): `every <N>m|h|d`, `daily HH:MM`,
`weekdays HH:MM`. Lifecycle for a recurring task:
```
scheduled (waiting, next_run_at set)
   └─ scheduler tick: due & not paused → status=ready, recompute next_run_at
        └─ Dispatcher claims → running → (success) back to `scheduled`  (NOT `review` — no human gate per run)
                                       → (failure) `blocked` (paused; surfaced on the board)
```
A non-recurring task is unchanged (success → `review`).

## The tick (`scheduler.py`)
A single daemon thread started at boot (`server.py main`, only in the real server — never in tests).
Every `SCHEDULER_TICK_SECONDS` (default 30s): find recurring, un-paused, `scheduled` tasks with
`next_run_at <= now`; flip each to `ready` and set the next `next_run_at`; then call
`dispatcher.dispatch()`. Idempotent and crash-safe (next_run is advanced *before* the run, so a restart
mid-run never double-fires).

## Lead-facing tool (`schedule_task`) — gated
A normal discovered skill (I-1) so the lead can create schedules **by talking**:
`CATEGORY="write"`, `RISK_LEVEL="medium"`, `REQUIRES_APPROVAL=True`, `ENABLED_BY_DEFAULT=False`,
`SOURCE="dtm_ai"`. It reaches the board via `ctx._meta["tasks"]` (the TaskStore injected at
context-build time). Because it is a write tool, `dispatch()` routes it through the Capability
Console + approval gate: creating scheduled work needs the owner's **allow_write** and (until the owner
trusts it) an **approval token** — the agent cannot silently schedule itself recurring work. Tenant is
bound by `ctx`; cross-tenant scheduling is rejected. Args: `title`, `instructions`, `assignee` (profile),
`schedule` (spec). Returns the created task id.

## Safety floors (unchanged, Rule #1)
Every scheduled run still flows through `dispatch()` (audited, read-only floor on client systems,
tenant isolation, validation). A specialist on a recurring job has exactly the same guarded tool access
as in chat — the schedule only decides *when* and *which brain*, never *what it may touch*.

## Board UI
The `scheduled` column shows recurrence: the spec, next run, a paused badge, and per-card **Run now /
Pause / Resume / Delete**. Task detail already lists `runs` (the occurrence history).
