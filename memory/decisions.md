# decisions.md — Architectural Decisions (with reasons)

> Format: **D-N — Decision** · _Reason_ · (status)

## Pivot — fully in-house, Hermes removed (2026-06-09)

**D-19 — Remove the Nous Hermes runtime entirely; build the brain, specialist profiles, memory,
delegation, and learning skills NATIVELY inside DTM AI as one unified system.**
_Reason: owner wants a wholly in-house build with no third-party agent runtime to depend on or trust
("it's our thing — we make the rules"). Inventory showed the native brain already exists and is the
default engine (`execution/agent.py` bounded tool-call loop + `router.py` + `dispatch.py` + `memory.py`
VaultStore + `builder.py` sandbox); Hermes was only an alternate chat engine (`hermes_bridge.py`) + a
delegation path (`hermes_kanban.py` + a root-owned sudo wrapper). So removal is mostly delete+repoint,
not build-from-scratch. Net security/control win: deletes a third-party agent runtime with
terminal/code/file/browser capability, the Docker fence whose sole purpose was containing that runtime,
and the root-owned sudo kanban wrapper + docker-exec bridge. Native also unlocks per-profile chat
(Hermes' api_server had no per-profile selector — the only reason delegation had to go through a kanban
board, D-18)._
**Supersedes:** D-12 (Hermes as the brain), D-17 (Docker fence for Hermes), D-18 (delegation via Hermes
kanban + wrapper), and the "keep agent execution in a separate fenced process" part of D-1. **The
FastAPI backend ⇄ TypeScript dashboard split (rest of D-1) stays.**
**Preserved (these protect CLIENTS/TENANTS, never were about Hermes):** read-only by default; audit
every call; tenant isolation absolute (Rule #4); validate tool args (Rule #3); no free-form shell
(Rule #6); fail closed (Rule #8); secrets fingerprint-only (I-3); config kill-switch (I-4); git
rollback (I-6); SOP-before-code (I-7).
**I-5 stance:** the "separate sandboxed coding agent / no shared creds" framing was anti-Hermes and is
relaxed; KEEP a one-click human-merge gate for brand-new EXECUTABLE primitives (LLM-written code that
touches live client systems is the highest-risk surface). Learned skills = playbooks composing already-
enabled primitives (no new code) → no merge gate, per D-4-reframed-by-D-15.
**Defaults taken (owner dismissed the option prompts; chosen + stated, override anytime):** big-bang
cutover (no parallel Hermes fallback); delegation store = native `TaskStore` following the existing
SQLite-dev/Postgres-prod store pattern (not premature Postgres, not a standalone board file); learning
skills = playbooks. _(locked by owner 2026-06-09; build in 6 phases — see task_plan.md CURRENT FOCUS.)_

**D-20 — Per-client memory is a LIVING, EDITABLE document, not an append-only log.** Both the agent
and the owner can READ and OVERWRITE `clients/<tenant>/memory.md` to correct/update/prune facts as the
environment changes (firewall upgraded, computers swapped, a contact leaves, a fact was wrong).
_Reason: owner — "it should not be append only, it should be updatable as things change." An MSP
environment is mutable; a timestamped append log accumulates stale, contradictory facts and the agent
would carry them forever. A current-state doc the agent maintains is what "remembers context like an
employee" actually means._ **How:** new `memory_update(content)` tool (write/internal, enabled by
default, no approval — vault-only, never a client system) overwrites the whole doc after a `memory_read`;
`memory_note(note)` still ADDS a fact. `write_memory` keeps `memory.md.bak` (one-step rollback) and every
write is audited. Dashboard Memory tab edits `memory.md` directly via an editable textarea. **Safety
floors unchanged:** tenant isolation, `*` rejected, internal-write-≠-client-write, Capability-Console
toggle, full audit. _(locked by owner 2026-06-09; SOP: architecture/memory-vault.md.)_

**D-21 — Admin-only Terminal tab: a fenced, human-only exception to Rule #6 ("no free-form shell").**
A logged-in **admin** can run shell commands on the host from a dashboard Terminal tab, as a convenience
over opening SSH. _Reason: owner wants quick command access on the box without SSH; basic users (future)
won't see it. Owner made the call with the risk explained ("it's our thing — we make the rules")._
**Scope of the exception:** it applies to HUMANS, not the AI — the agent loop still has zero shell access
(Rule #6 for the agent is unchanged). **Guardrails (in code, not prose):** admin-gated route + nav item
(non-admins can't see or call it); every command written to the append-only audit log (`action=terminal`)
BEFORE it runs; executes as the unprivileged `dtm-ai` service user (no sudo wired) so root actions still
need SSH-as-ross; real containment is the systemd sandbox (`ProtectSystem=strict` +
`ReadWritePaths=/opt/dtm-ai`); per-command timeout (30s) + output cap (100k); instant kill switch
`DTM_ADMIN_TERMINAL=0` (I-4). **Accepted residual risk:** a stolen admin session / XSS = command exec as
`dtm-ai`, which can read the app + vault and write within `/opt/dtm-ai`. Not an interactive PTY (no
vim/top); `cd` persists per user. Code: `core/adminshell.py`, routes `GET/POST /api/terminal`; SOP:
architecture/admin-terminal.md. _(locked by owner 2026-06-09.)_

**D-22 — Widen the admin Terminal to FULL ROOT, add an independent recovery console on :8091.**
Extends D-21 at the owner's explicit, informed direction ("full root access or whatever access... no
blocks... and a second port so if the site goes down during an update I can switch over"). _Reason: it's
the owner's own box and a necessary admin tool; risks were laid out twice and accepted._
**What changed:**
- **No blocks:** terminal timeout removed by default (`DTM_TERMINAL_TIMEOUT=0` → none); output still capped
  (`DTM_TERMINAL_MAXOUT`, default 1 MB) only so a runaway command can't OOM the response. Audit logging
  kept — it records, it doesn't block.
- **Full root:** via `sudo`, granted by a `NOPASSWD: ALL` sudoers file for `dtm-ai`
  (`deploy/sudoers-dtm-ai-terminal.snippet`) + a drop-in that relaxes the systemd sandbox so sudo can act
  (`deploy/dtm-ai.service.d/10-full-access.conf` — turns off NoNewPrivileges / RestrictSUIDSGID /
  ProtectSystem). The web app process itself stays `dtm-ai` (root is opt-in per `sudo` command).
- **Failover:** an INDEPENDENT, **terminal-only** root console on :8091 (`execution/web/recovery.py` +
  `deploy/dtm-ai-recovery.service`, runs as root). Serves ONLY a login page + a terminal — no dashboard,
  no app APIs. Separate process → survives the main app crashing/restarting; the deploy flow must NOT
  restart it, so it keeps running during a broken update. Same login (shared `.session_secret` + users DB).
**Owner must apply the privileged parts** (sudoers / drop-in / unit install) as root — I can't take root.
**Still true:** admin-only; the AI/agent loop has ZERO shell access (Rule #6 for the agent unchanged);
every command audited; `DTM_ADMIN_TERMINAL=0` kill switch.
**Accepted residual risk (explicit):** a stolen admin session, CSRF, or an XSS hole = full root + total
server/all-client-data takeover — and the channel is currently plain HTTP on the LAN (no TLS on the box
yet). Mitigations to revisit: put TLS in front; consider scoping sudo later; keep admin creds tight.
_(locked by owner 2026-06-09; SOP architecture/admin-terminal.md.)_

## Locked by user (2026-06-01 Blueprint discovery)

**D-1 — Two-process split: Python FastAPI agent backend + TypeScript dashboard, joined by a typed
REST + WebSocket boundary. Not merged.**
_Reason: the proven brains (registry, credential layer, vendor clients) are Python; the polished UI is
TS. Keeping them separate keeps privileged work (vendor creds, agent execution) out of the browser/edge
and lets each evolve independently._ (locked)

**D-2 — Tenancy: one shared multi-tenant instance.**
_Reason: DTM manages many clients; a single instance with `tenant_id` on every row + Postgres
row-level security is easiest to operate and grow. Designed so a sensitive client can be extracted to an
isolated deployment later if ever needed._ (locked)

**D-3 — Model posture: local-first, cloud opt-in per task.**
_Reason: client data sensitivity. Local Ollama handles anything touching client data by default; routing
to Claude/OpenAI requires the task to be flagged non-sensitive or explicitly approved. The model router
is the enforcement seam._ (locked)

**D-4 — Self-coding gate: human merge required for EVERY generated tool. No auto-promote.**
_Reason: user wants to stay fully in the loop; "changes reviewed before activated." A generated tool
only goes live after schema-lint + tests + security-scan + human merge into `skills/` AND enable-in-config._
(locked)

**D-5 — UI hosting: co-located on the Ubuntu server behind nginx/HTTPS.**
_Reason: simplest, fully on-prem, no edge dependency, matches the existing hardened systemd deploy._ (locked)

## Architect recommendations (default unless user overrides)

**D-6 — State: PostgreSQL (per-client RLS) + Redis (cached vendor tokens + rate-limit windows).**
_Reason: replaces the shared autocommit SQLite connection (concurrency hazard) and enables real
multi-tenant isolation._ (proposed)

**D-7 — Guardrails enforced in code at `dispatch()`, not prose.** read/alert run freely; write/destructive
blocked unless (a) tool enabled in config, (b) per-client feature flag on, (c) one-shot human approval
token present for that action. LLM tool args validated against `PARAMETERS` (JSON-Schema) before `run()`.
_Reason: the #1 gap in the existing build; this is the core security upgrade._ (proposed)

**D-8 — Self-improvement isolation: runtime agent CANNOT author/modify tools; a separate sandboxed coding
agent (no prod creds, no prod DB, synthetic tenant) writes to `skills_candidate/` staging only.** Registry
+ config is the safety boundary; disable-by-config is the instant kill switch; everything under git for
backup/rollback; new tools default `CATEGORY=read`, `ENABLED_BY_DEFAULT=False`.
_Reason: the "it must not break itself" requirement. A generated tool cannot reach the running platform
without passing the gate._ (proposed)

**D-9 — Secret management v1: SOPS-encrypted env (or OS keyring) + 0600 + fingerprint display; design a
clean seam to swap in Vault/KMS later.** _Reason: no plaintext secrets, but don't over-build infra in v1._
(proposed — pending user confirmation)

**D-10 — TS types generated from the FastAPI OpenAPI schema; snapshot validated with zod on the frontend.**
_Reason: single-source the API contract; the dashboard never trusts `any`-typed data._ (proposed)

## Evolved model — graduated autonomy + Hermes + Obsidian (2026-06-01, second session)

> The owner read the Nous Research **Hermes Agent** docs and wants it as the brain, with a
> control panel to open capabilities gradually toward autonomy. NOTE: "Hermes" now means
> **Nous Research Hermes Agent** (github.com/NousResearch/hermes-agent) — a real, separate
> thing from the `ClaudeOS [Hermes] V2` folder (which remains only a UI/design donor).

**D-11 — Graduated Capability model with a Capability Console + defense-in-depth.**
Replaces "read-only by construction (DenyAllApprovals)" with "read-only by DEFAULT, tunable
per tool by the owner." Three layers: (1) PRIMARY = capability policy per tool
{enabled, allow_write, require_approval} set in the console; (2) BACKUP = least-privilege
vendor API creds (start read-only where the API supports it); (3) ALWAYS-ON SAFETY FLOORS
that the console cannot disable: audit every call, tenant isolation, secrets fingerprint-only,
destructive tools always require a per-action approval token, and NEW-tool authoring still
needs human merge (D-4). _Reason: owner wants the agent to "eventually work on its own";
correction logged — upstream API scoping alone is insufficient (some vendors issue one
read+write key), so our gate stays the primary throttle._ (locked; implemented in
`core/capabilities.py` + `core/gates.py`, tested)

**D-12 — Hermes Agent as the brain, fenced behind our tool layer via MCP.**
Hermes brings memory, self-improving skills, and multi-channel reach. It reaches client
systems ONLY through DTM AI's tools exposed as an **MCP server**, so dispatch()'s guardrails
apply no matter how capable/autonomous Hermes is. Hermes' own native toolsets (terminal,
execute_code, file, browser, memory, web) are surfaced as entries in the SAME Capability
Console and start mostly off, enabled as trust is earned. _Reason: get Hermes' power without
giving an autonomous agent unguarded access to client environments._ (locked; build seam =
MCP server wrapping the registry)

**D-13 — Obsidian as a fresh memory + knowledge-base vault.**
Start a new Obsidian vault as DTM AI's knowledge base (per-client runbooks/SOPs) AND the
agent's human-readable long-term memory (`Clients/<tenant>/memory.md`). Markdown on disk →
git-trackable, backup-able, human-editable, auditable. Integrate read-only first (kb_search),
then guarded writes for memory notes. Modeled on Hermes' MEMORY.md/USER.md + FTS5 approach.
_Reason: owner has no central KB yet; pure-upside, no security tension._ (locked)

**D-4 clarification:** D-4 ("human merge for every generated tool") governs AUTHORING a NEW
tool (the self-coding agent). It is distinct from D-11's runtime toggling of an EXISTING
tool's capability. Both stay true: you can open an existing tool's writes from the console,
but a brand-new tool still requires sandbox + human merge to exist at all.

## Skill model — no hand-coded tools; learned skills over guarded primitives (2026-06-01)

**D-15 — The owner will NOT hand-code tools. Capabilities grow as LEARNED SKILLS that compose a
small, trusted set of guarded PRIMITIVES.** Two layers: (1) Primitives = guarded low-level
capabilities that touch client systems (hold creds, decide read/write) — trusted code, built once by
the maintainer, NOT AI-improvised; they are the security boundary. (2) Learned skills = unlimited
reusable procedures the AI/Hermes composes from *enabled* primitives and saves for reuse — no human
coding. Safe because a learned skill can only combine already-enabled primitives; it can't invent new
access. Human control is at the primitive/Capability-Console layer, not per learned skill.
**Primitive layer chosen: scoped generic read connectors** (`kaseya_read`/`cylance_read`/`huntress_read`
+ `clients/scopes.py`): arbitrary path but GET-only + per-vendor read-path allowlist, blocks
auth/host-escape/out-of-scope, blocked path never calls the client (tested). Writes stay separate,
individually-gated primitives. _Reason: realizes the owner's "no hand-coding, all learned skills"
vision while keeping a hard, owner-controlled security boundary._ (locked; implemented + tested. SOP:
`architecture/skill-model.md`.)

**D-4 reframed by D-15:** the human-merge gate was about generated *Python primitives*. Learned skills
are compositions (no new code) → no merge gate; control moved to the primitive layer. A brand-new
*primitive* (new vendor/write op) is still deliberate trusted code, added by the maintainer — not
hand-coded by the owner in normal operations.

## Deployment (2026-06-01)

**D-14 — Deploy via the existing `trinichad/KaseyaLink` GitHub repo, renamed to DTM-AI.**
The server already pulls from `github.com/trinichad/KaseyaLink` (origin/main) for the old Kaseya AI
app. Plan: rename that repo → DTM-AI (GitHub redirects the old URL, so the server's existing clone
keeps pulling with no re-clone), push the DTM AI build onto main (old app files removed but preserved in
history), tag the last old commit `v0-kaseya-link`. _One-time server migration required either way
(new entrypoint `python3 -m execution.web`; `.env` key remap KASEYA_URL→KASEYA_BASE_URL,
KASEYA_PASS→KASEYA_PASSWORD, add CYLANCE_*/HUNTRESS_*). After that, every update = `git pull && restart`._
**This is a deliberate Phase-T cutover — NOT done yet.** Do NOT touch the live repo/server until the
owner says "deploy". We keep building the clean code in the meantime. (locked; deferred)

## Resolved Blueprint questions (2026-06-01)
- **North Star** — read-only conversational assistant for the team to check things across all clients
  (option 1a). Write actions deferred to Phase 5 behind approval gate. (locked)
- **Phase-L credential inventory** — GREEN today: **Kaseya VSA, Cylance, Huntress** (all three already
  have working clients in Kaseya Link). M365/Entra next. Everything else = read-only stubs for Phase 3.
  (locked)
- **D-9 secret management** — defaulting to SOPS-encrypted env / OS keyring + 0600 + fingerprints for v1
  unless user names an existing vault. (assumed; flag if wrong)

## Hermes execution fence — Docker, not a dedicated user (2026-06-04)

**D-17 — Run Hermes inside a Docker container as the execution fence; share data (not creds) via a
host volume.**
The fence that matters is the OS/process boundary, NOT Hermes' tool config. Proven this session:
`agent.disabled_toolsets` (terminal/code/file/cronjob) does NOT propagate to delegated **sub-agent
workers** — a delegated worker still ran `whoami` as `ross`, and `ross` has scoped sudo to `dtm-ai`
(the creds owner). So disabling tools in config is not a real boundary, and disabling `delegation`
would break the owner's manager→specialist plan. The clean fix (a dedicated powerless `hermes` user)
needs root, which the owner does NOT have on the box. **Docker is the no-root fence:** the container
confines the agent's shell (no `/opt/dtm-ai`, no sudo), so native tools can stay ON without risking the
creds. Data is shared deliberately via a host volume `/srv/hermes-data` → `~/.hermes`, so DTM AI (on the
host) can read/display/edit skills, profiles, SOUL, and memory, and they stay model-agnostic. Two wired
channels: OUT = Hermes→DTM AI MCP over **HTTP** (`mcp_server.py` must gain an HTTP transport so the MCP
server + creds stay on the host); IN = DTM AI chat→Hermes API (published localhost port). _Reason: get a
real security boundary without root, keep every capability, and integrate Hermes' files into the DTM AI
UI. Supersedes the earlier "lock down native toolsets in Hermes config" approach (D-12's native-toolset
control) for THIS deployment — the container is the boundary; the Capability Console still governs the
MSP tools._ (locked; pending Docker-group grant — see task_plan.md "Current Focus")

**D-17 correction (2026-06-05) — the "no root" premise was inaccurate; `ross` has FULL sudo.**
`sudo -n -l` on the box shows `ross` is in the `sudo` group with `(ALL : ALL) ALL` (password-gated) plus
`(dtm-ai) NOPASSWD: ALL` and scoped NOPASSWD systemctl for the `dtm-ai` service. So: (a) the owner can
self-grant docker (`sudo usermod -aG docker ross`) — no admin needed; docker is already `enabled` on boot;
(b) the abandoned "dedicated powerless `hermes` user" fix is actually possible after all (would need a
password'd sudo). **Owner reaffirmed Docker (2026-06-05).** The fence is still genuinely required: the
`(dtm-ai) NOPASSWD: ALL` line means ANY process running as `ross` (incl. a Hermes delegated worker with a
terminal) can `sudo -u dtm-ai` with NO password and read the live client creds under `/opt/dtm-ai`. The
container shell can't reach host sudo or `/opt/dtm-ai`, so it closes that exact path.

**D-17 build (2026-06-05) — MCP HTTP transport added (the OUT channel prerequisite).**
`execution/mcp_server.py` now has BOTH transports: stdio (unchanged) and HTTP
(`--transport http --host <bridge> --port 8089`). **Tenant bound by URL path** — `/mcp`→`*`,
`/mcp/<client>`→that client — preserving the per-tenant fence one process (no process-per-client needed);
a `tenant_id` smuggled in args is ignored (path wins, tested). Optional `DTM_MCP_TOKEN` → `Authorization:
Bearer` required on POST; GET `/health` open. One shared agent serves all tenants. Tested (14 MCP tests inc.
real loopback HTTP; full suite 174 green). SOP `architecture/hermes-integration.md` + deploy kit
`config.snippet.yaml` updated with the container `url:` form. Bind the docker-bridge IP (not 0.0.0.0/not
127.0.0.1); container reaches it via `host.docker.internal`. Creds stay on host, never in the container.

**D-18 — Delegation runs on Hermes' kanban board; DTM AI reads it directly + delegates via a
locked-down privileged wrapper (2026-06-05).**
Real cross-profile delegation in Hermes is the **kanban** board (durable shared SQLite; a task assigned
to a profile is executed by a worker the gateway dispatcher spawns in an isolated workspace, running as
that specialist). Verified live: a task assigned to `sentinelops` spawned a worker that identified as
SentinelOps and saw only the `mcp_dtm_all_*` fenced tools. _Two design choices:_
- **Reads = direct, read-only.** The board DB is on the shared volume (`/srv/hermes-data/kanban.db`,
  `dtm-ai`-owned), so DTM AI opens it `mode=ro` — no `docker exec`, same as reading profiles. The
  api_server has **no per-request profile selector** (confirmed in source), so routing chat to a
  specialist isn't possible; kanban is the path.
- **Writes = a tiny root-owned wrapper, not direct DB writes.** Writing the DB directly would bypass
  Hermes' atomic-claim + event invariants, and the web service can't `docker exec`. So `dtm-ai` runs
  ONE root-owned script (`/usr/local/sbin/dtm-ai-kanban.sh`) via scoped NOPASSWD sudo. _Reason it's safe
  despite the D-17 escalation reality:_ the script is **root-owned (dtm-ai can't edit it)**, whitelists
  only `create`/`assign`/`dispatch`, validates every arg, uses **no shell** (argv array to `docker
  exec`), and only ever touches the agent's own board — never client systems. Delegating a task ≠ acting
  on a client. Owner-gated + audited. (212 tests green.)

## Streaming chat (2026-06-03)

**D-16 — Stream chat over Server-Sent Events (SSE), not raw WebSocket.**
The constitution's stack table (§6) lists WebSocket for the chat stream. For streaming a chat answer
the data flows only server→browser (the user sends the message via a normal POST; tokens + tool events
stream back), which is exactly SSE. Implementing RFC-6455 WebSocket framing by hand in the stdlib
`http.server` (no deps) is fragile and far more code; SSE is a few lines over the existing server and
robust. Chosen for the same "no deps, hard to break, runs identically dev+prod" reason the whole web
layer is stdlib. Implementation: `POST /api/chat/stream` returns `text/event-stream` with
`Connection: close` + `X-Accel-Buffering: no` (so nginx doesn't buffer); the browser reads frames via
`fetch().body.getReader()`. Provider token streaming for Ollama (NDJSON) and Claude (Anthropic SSE);
OpenAI/Mock fall back to whole-answer (later refinement). The agent's push-callback is bridged to the
SSE pull-generator via a queue + worker thread. **If true bidirectional WS is later needed for
server-pushed alerts, SSE-for-chat does not preclude adding it.** (locked; supersedes the §6 "WebSocket"
note for chat)
