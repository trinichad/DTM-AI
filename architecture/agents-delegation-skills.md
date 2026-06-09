# Agents, Delegation & Learned Skills — the native Brain layer (SOP)

> Supersedes the former `hermes-integration.md`. As of **D-19** DTM AI runs entirely on its own
> agent loop — no external runtime, no `docker exec`, no privileged sudo wrapper. This is the
> How-To for the brain layer (A.N.T. golden rule: update this SOP before the code).

## Agent loop (N-layer)
`execution/agent.py` — a bounded tool-call loop (default max 8 rounds), streaming + non-streaming,
history compaction, citations, approval gate. `build_system_prompt(profile)` layers a profile's
SOUL + long-term memory **below** the immutable safety contract (`SYSTEM_PROMPT`); an unknown/blank
profile or any read error falls back to the plain base prompt. Tools run **only** through
`dispatch()` (Rule #1–#8 enforced there). Model selection via `core/router.py` — Ollama local by
default; Claude/OpenAI only with `allow_cloud` (Rule #5, local-first).

## Profiles (the team)
`core/agents.py` reads/writes agent personas on disk. Layout: the **AtlasOps Manager** is the
`default` profile at the agents-dir root; **specialists** live under `profiles/<name>/`. A profile
is human-editable markdown + yaml: `SOUL.md` (persona/role), `profile.yaml` (routing description),
`config.yaml` (preferred model), `memories/`, `sessions/`, `skills/`.
Agents-dir resolution order: `DTM_AGENTS_DIR` → legacy `DTM_HERMES_DATA_DIR` /
`DTM_HERMES_SKILLS_DIR` (migration grace so a running deploy keeps reading its profiles) →
`<vault>/agents`. The manager's roster auto-syncs (a `TEAM:AUTO` block in the default SOUL) on every
create / delete / soul-edit, so AtlasOps always knows who it can delegate to.

## Delegation (the board)
`core/tasks.py` — `TaskStore` (SQLite dev / Postgres prod; same store pattern as Audit/Approval/
Conversation) + `Dispatcher`. Lifecycle: created **with** an assignee → `ready`; unassigned →
`triage`. `ready` → (atomic `claim_next_ready`) `running` → `review` (worker answered) | `blocked`
(worker errored, `consecutive_failures++`). A worker runs `agent.py` **as** the assigned profile,
bound to the task's tenant, local-first. UI + API: the Delegation board (`/api/kanban*` routes, now
native). No docker exec, no sudo wrapper.

## Learned skills (playbooks)
`core/playbooks.py` — a learned skill is a reusable **procedure** that composes tools already in the
registry (D-15: no new code, so it can't invent access). Stored as markdown in
`<vault>/skills/<slug>.md`. The `skill_search` tool (a read primitive) lets the agent find an
existing skill **before** re-deriving one; the system prompt nudges it to check first. After a
multi-step turn the chat answer carries `suggest_skill`; the owner confirms via
`POST /api/skills/learn`, which **dedups** (slug collision or ≥0.6 term overlap → returns the
existing skill instead of a twin). Brand-new **executable** primitives still go through the
`builder.py` sandbox + **human merge** (I-5) — that gate stays even fully in-house.

## Self-annealing
On any failure: read the real error/stack (no guessing), patch `execution/`, test the fix, then
write the lesson into **this SOP** so it never repeats. Disable-by-config (I-4) is the emergency
stop; git (I-6) is the rollback.
