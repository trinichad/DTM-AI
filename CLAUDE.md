# DTM AI — Project Constitution

> Private AI operations platform for **DTM Consulting** (IT MSP).
> Built with the B.L.A.S.T. protocol + A.N.T. 3-layer architecture. Reliability over speed.
> Living state lives in `/memory/`. This file is the law the system is built against.
> (The System Pilot operating protocol is inherited from the parent `Projects/CLAUDE.md`.)

---

## 0. North Star

A **secure, read-only conversational assistant** the DTM team chats with (text + voice) to check things
across **all client environments at once** — devices, users, security posture, backups, alerts — and get
**instant, sourced answers**. v1 reads and reports; it does **not** change client systems. Write actions
are a deliberate later phase behind a hard approval gate.

**We win v1 when:** a DTM tech can open the dashboard, pick a client (or "all"), ask a plain-English
question, and get a correct answer assembled from live Kaseya/Cylance/Huntress data, with every fact
traceable to the tool that produced it — and a non-developer can disable any tool or integration in one
click.

---

## 1. Architecture (A.N.T. + two-process split)

```
┌─────────────────────────────────────────────────────────────────┐
│  DASHBOARD (TypeScript)  — TanStack Start + Tailwind v4 + shadcn   │
│  chat · voice · client/model/task selectors · tools · audit · logs│
└───────────────▲───────────────────────────────────▲──────────────┘
        REST/JSON (reads, CRUD)          WebSocket (chat stream, alerts)
        zod-validated, typed from OpenAPI   token-gated
┌───────────────┴───────────────────────────────────┴──────────────┐
│  BACKEND (Python · FastAPI)                                        │
│                                                                   │
│  N — NAVIGATION   model router + agent loop (decides, routes,     │
│                   never does heavy work itself)                   │
│  T — TOOLS        skills/ auto-discovery registry  → dispatch()   │
│                   enforces CATEGORY + schema validation + audit   │
│  credentials.require()  ·  per-tenant scoped clients              │
│                                                                   │
│  Postgres (tenant_id + RLS)  ·  Redis (token/rate-limit cache)    │
│  Ollama (local LLM, default)  ·  Claude/OpenAI (opt-in per task)  │
└───────────────────────────────────────────────────────────────────┘
A — ARCHITECTURE: /architecture/*.md SOPs (the How-To; update SOP before code)
```

**Two processes, two privilege sets** (D-1). The browser never holds vendor secrets; privileged work
(creds, agent execution) stays in the backend on the Ubuntu box behind nginx/HTTPS (D-5).

---

## 2. Data Schemas (Data-First — input shape → output shape)

### 2.1 Tool/Skill definition (every `skills/*.py` module exports these)
```python
NAME: str                 # snake_case, unique, e.g. "kaseya_list_assets"
DESCRIPTION: str          # one line, shown to the LLM
PARAMETERS: dict          # JSON-Schema object — VALIDATED before run() (no longer trusted)
CATEGORY: str             # "read" | "alert" | "write" | "destructive"  — ENFORCED in dispatch()
RISK_LEVEL: str           # "none" | "low" | "medium" | "high"
REQUIRES_APPROVAL: bool   # write/destructive default True
ENABLED_BY_DEFAULT: bool  # generated tools default False
def run(ctx, **kwargs) -> dict | list   # ctx = {tenant_id, clients, actor}; returns JSON-serializable
```

### 2.2 Chat turn
```jsonc
// INPUT  → POST /api/chat  (or WS frame)
{ "tenant_id": "acme",        // or "*" for all clients
  "session_id": "uuid",
  "message": "which acme users have MFA disabled?",
  "model_hint": null,          // null = router decides
  "allow_cloud": false }       // must be true (or task flagged non-sensitive) to leave local LLM
// OUTPUT → streamed frames
{ "type": "tool_call",  "name": "...", "category": "read", "args": {...} }
{ "type": "tool_result","name": "...", "ok": true, "source": "kaseya_vsa", "data": {...} }
{ "type": "answer",     "content": "...", "citations": ["kaseya_list_assets@acme"] }
```

### 2.3 Tool result envelope (uniform; failures never raise)
```jsonc
{ "ok": true|false, "source": "<integration>", "tenant_id": "...",
  "data": <json> | null, "error": "<message>" | null, "latency_ms": 123 }
```

### 2.4 Audit log entry (append-only — written for EVERY call, reads included)
```jsonc
{ "ts": "ISO-8601", "actor": "user@dtm", "tenant_id": "acme",
  "action": "tool_call|approval|login|config_change|credential_view",
  "tool": "kaseya_list_assets", "category": "read",
  "args_hash": "sha256(...)", "args_json": "{…} (≤2 KB copy for owner review — D-24)",
  "result_ok": true, "approval_id": null }
```

### 2.5 Snapshot (dashboard live-data — one normalized JSON per client, zod-validated on FE)
```jsonc
{ "tenant_id": "...", "generated_at": "ISO", "is_demo": false,
  "integrations": [ { "name": "kaseya", "ok": true, "latency_ms": 80 } ],
  "summary": { "devices": 0, "users": 0, "open_alerts": 0, "mfa_gaps": 0, "backup_failures": 0 } }
```

---

## 3. Behavioral Rules (hard — enforced in code, not just prose)

1. **Read-only by default, graduated by the owner (Capability Console).** Tools start read-only;
   `write`/`destructive` are blocked at `dispatch()` unless the owner has opened them in the Capability
   Console (`allow_write`) **and** the approval policy is satisfied. Autonomy may ramp per tool
   (`require_approval=False` lets a *trusted, non-destructive* write run unattended). **Safety floors that
   the console can NEVER disable:** every call is audited; tenant isolation holds; secrets stay
   fingerprint-only; `destructive` tools ALWAYS require a per-action approval token; authoring a NEW tool
   still needs sandbox + human merge. Defense-in-depth: least-privilege vendor creds are a backup layer,
   never the only control.
2. **Never invent identifiers or facts.** If a tool didn't return it, the assistant says it doesn't know.
   Every answer cites the tool(s) + tenant it came from.
3. **Validate before running.** LLM tool args are checked against `PARAMETERS` (JSON-Schema, required
   fields, enums) before `run()`. Mismatch → reject, feed error back to the model.
4. **Tenant isolation is absolute.** A session is bound to one tenant (or explicit "*"); a tool can only
   construct clients for its own tenant. Cross-tenant calls are rejected before any client is built.
5. **Local-first.** Tasks touching client data run on the local LLM by default; routing to a cloud model
   requires `allow_cloud=true` or an explicit non-sensitive flag (D-3).
6. **No free-form shell for the AGENT, ever.** The agent runs only registered, parameterized tools.
   No LLM-emitted command strings are executed. *(Exception — humans only, D-21 + D-22):* an
   **admin-only, audited** Terminal lets a logged-in admin run commands on the host (a convenience over
   SSH), with **full root** at the owner's direction (D-22) — via `sudo` (NOPASSWD) + a relaxed systemd
   sandbox, and an independent root recovery console on **:8091** that survives the main app going down.
   It is never reachable by the LLM/agent loop; it is admin-gated, every command is logged before it
   runs, and `DTM_ADMIN_TERMINAL=0` kills it instantly (I-4). The agent's no-shell rule is unchanged;
   the privileged grants are installed by the owner as root (`deploy/`).
7. **Decline + log.** On any destructive request, ambiguity, or missing approval: refuse, explain, log.
8. **Fail closed.** Missing credential → no client built. Missing approval → no write. Auth/crypto error →
   deny. Never degrade to an anonymous or partial-privilege call.

---

## 4. Architectural Invariants (do not violate without a decisions.md entry)

- **I-1** Tools are discovered, never hand-registered: a `skills/*.py` with the §2.1 attrs is the only way
  to add capability. Missing a required attr → silently skipped.
- **I-2** `credentials.require()` is the *only* path to a vendor client; it fails closed on any missing key.
- **I-3** No plaintext secrets in code or git. Secrets via SOPS-encrypted env / OS keyring, file mode 0600,
  surfaced only as `sha256[:7]` fingerprints. Boot refuses if the secret file is group/world-readable.
- **I-4** Enable/disable is **config, not code** — flipping a tool off in config makes `dispatch()` refuse
  it even if the model names it. This is the instant kill switch.
- **I-5** The runtime agent may **save learned-skill playbooks** (compositions of already-enabled tools,
  no new code — D-15) but **cannot author or modify executable tool code.** A new executable primitive is
  drafted into `skills_candidate/` (AST security-scan + schema-lint) and reaches live `skills/` only by
  **human merge or direct admin edit (D-23)** — LLM-written code touching live client systems is the
  highest-risk surface, so the sandbox gate stays for the AGENT (D-4, D-19). The **human owner** may
  edit/rename/delete/add skills directly from the Capabilities tab (admin-gated, audited, validated
  before going live, git-tracked) — the owner is the trust anchor, not the threat model. New tools
  default `CATEGORY=read`, `ENABLED_BY_DEFAULT=False`.
- **I-6** Everything under git. Tool promotions and config changes are commits → backup + rollback by design.
- **I-7** If logic changes, the `/architecture/` SOP is updated **before** the code (A.N.T. golden rule).
- **I-8** All intermediate/ephemeral file IO routes through `/.tmp/`. Deliverables land in the
  dashboard/DB/report — never left in `/.tmp/`.

---

## 5. B.L.A.S.T. Phase Outputs

- **B — Blueprint:** North Star §0; integrations = Kaseya VSA, Cylance, Huntress (green today), M365/Entra
  next; source of truth = each client's live vendor APIs; payload = sourced answers in the chat UI +
  on-demand reports; behavioral rules §3. ✅
- **L — Link:** verify Kaseya/Cylance/Huntress via `credentials.require()` + per-integration probes before
  any logic. Broken link = halt. _(Phase 1)_
- **A — Architect:** §1; A=`/architecture/` SOPs, N=model router + agent loop, T=`skills/` registry. _(Phase 1–2)_
- **S — Stylize:** Hermes-donor UI (sidebar/header/status-pills/dials); every output ships a verify step;
  UI must visibly distinguish *analyzing* vs *about to act*. _(Phase 1+)_
- **T — Trigger:** systemd on Ubuntu behind nginx/HTTPS; scheduled audits via cron installer;
  self-annealing repair loop writes each lesson back into the matching `/architecture/` SOP. _(Phase 4)_

---

## 6. Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11, FastAPI, uvicorn |
| Agent | model router (Ollama local default; Claude/OpenAI opt-in) + bounded tool-call loop |
| State | PostgreSQL (tenant_id + row-level security), Redis (token/rate-limit cache) |
| Frontend | TanStack Start/Router/Query, Tailwind v4, shadcn/Radix (Hermes donor shell) |
| Contract | REST/JSON (reads/CRUD) + WebSocket (chat/alerts); TS types from FastAPI OpenAPI; zod on FE |
| Auth | bcrypt, signed sessions w/ TTL + rotation, admin-gated mutations |
| Secrets | SOPS-encrypted env / OS keyring + 0600 + fingerprint display (Vault/KMS seam for later) |
| Deploy | systemd (dedicated user, hardened) + nginx/HTTPS on the existing Ubuntu box |

---

## 7. Repo Layout

```
DTM AI/
├── CLAUDE.md            # this constitution
├── memory/              # task_plan · findings · progress · decisions  (living state)
├── architecture/        # A-layer SOPs (How-To, markdown)
├── execution/           # T-layer — backend (FastAPI, skills/, router, clients)  [HALT until sign-off]
├── dashboard/           # TS frontend (Hermes-donor shell)
├── skills_candidate/    # sandboxed coding-agent staging (never auto-live)
└── .tmp/                # ephemeral workbench
```

## 7b. The Brain layer — native agent loop, profiles, memory, learned skills (D-19)

- **Native agent loop (the brain).** `execution/agent.py` is DTM AI's own bounded tool-call loop —
  no external runtime. It runs as a chosen **profile** (specialist), loading that profile's SOUL +
  long-term memory as the system prompt and calling tools only through `dispatch()` (every guardrail
  in Rule #1–#8 applies). **Profiles** = agent personas on disk (`core/agents.py`): AtlasOps Manager
  (`default`) + specialists under `profiles/<name>/`, resolved from `DTM_AGENTS_DIR` (legacy
  `DTM_HERMES_*` fallback until migrated) else `<vault>/agents`. **Delegation** = the native
  `TaskStore` board + `Dispatcher` (`core/tasks.py`): a task assigned to a profile is run by a worker
  that IS this loop, as that profile, bound to the task's tenant. **Learned skills** = reusable
  PLAYBOOKS composing already-trusted tools (`core/playbooks.py`, the `skill_search` tool, D-15) — the
  agent reuses one before re-deriving; the owner confirms saves; dedup'd. *(Hermes removed — D-19; the
  `ClaudeOS [Hermes] V2` folder remains only a UI design donor.)*
- **Capability Console** = the owner's throttle. Per tool: `enabled`, `allow_write`, `require_approval`.
  Backend: `core/capabilities.py` + `core/gates.py`. Safety floors (Rule #1) are enforced in code, not
  in the console.
- **Obsidian vault** = knowledge base (per-client runbooks/SOPs → read-only `kb_search`) + the agent's
  human-readable long-term memory (`clients/<tenant>/memory.md`). Memory is a **living, editable record**
  of the client's current environment (D-20): both agent and owner READ + UPDATE it (`memory_read` /
  `memory_note` to add / `memory_update` to correct/prune; `.bak` rollback on overwrite) — not an
  append-only log. Markdown on disk: git-trackable, backup-able, human-editable. Per-profile
  MEMORY.md/USER.md feed the agent's system prompt.

## 8. Maintenance / Self-Annealing
On any failure: (a) read the real error/stack — no guessing; (b) patch in `execution/`; (c) test the fix;
(d) write the lesson into the matching `architecture/` SOP so it never repeats. Disable-by-config (I-4) is
the emergency stop; git (I-6) is the rollback.
