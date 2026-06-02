# decisions.md — Architectural Decisions (with reasons)

> Format: **D-N — Decision** · _Reason_ · (status)

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

## Resolved Blueprint questions (2026-06-01)
- **North Star** — read-only conversational assistant for the team to check things across all clients
  (option 1a). Write actions deferred to Phase 5 behind approval gate. (locked)
- **Phase-L credential inventory** — GREEN today: **Kaseya VSA, Cylance, Huntress** (all three already
  have working clients in Kaseya Link). M365/Entra next. Everything else = read-only stubs for Phase 3.
  (locked)
- **D-9 secret management** — defaulting to SOPS-encrypted env / OS keyring + 0600 + fingerprints for v1
  unless user names an existing vault. (assumed; flag if wrong)
