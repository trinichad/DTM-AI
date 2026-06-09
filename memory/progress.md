# progress.md — Work Log

## 2026-06-01
- Ran parallel recon workflow over `Kaseya Link` + `ClaudeOS [Hermes] V2` (4 readers + synthesis).
  Result: reuse/replace matrix + architecture brief → findings.md.
- User clarified Hermes V2 is a **design donor only**, not an architecture donor.
- Blueprint discovery — locked: D-2 shared multi-tenant, D-3 local-first/cloud-opt-in,
  D-4 human-merge-always, D-5 co-located on Ubuntu. → decisions.md.
- Scaffolded `/memory /architecture /execution /.tmp`; wrote findings/decisions/task_plan/progress.
- Locked North Star (read-only conversational assistant, 1a) + Phase-L greens (Kaseya/Cylance/Huntress).
- Wrote **CLAUDE.md Project Constitution** (data schemas, behavioral rules, invariants, B.L.A.S.T. outputs).
- **Next:** awaiting user sign-off on the phased plan → then begin Phase 1 (git init + FastAPI skeleton +
  config loader + Postgres schema + model router + ported registry + dashboard shell). No `/execution/`
  logic written yet (HALT respected).

### Phase 1 — Push 1: Secure backend core  ✅ (2026-06-01)
Built + verified the security-critical core, stdlib-only (runs with NO Postgres/Ollama/creds):
- `execution/core/`: context (tenant envelope), validation (zero-dep JSON-Schema), config
  (0600 enforce + fingerprints + fail-closed), credentials (require() + status), audit
  (sqlite append-only + tool enable/disable kill switch), registry (auto-discovery + to_schema),
  **dispatch (the guardrail chokepoint)**, router (local-first model selection).
- `execution/`: agent.py (bounded nav loop), runtime.py (wiring), cli.py (verify w/o web).
- `execution/skills/`: system_health, echo_note (safe read tools).
- **Tests: 34/34 pass** (`python3 -m unittest discover -s tests`). Proven in CODE:
  write tools blocked by default + never execute; bad args rejected pre-run; disabled tools
  refused; cross-tenant blocked; raising tools contained; every call audited w/ hashed args;
  router stays local unless cloud explicitly unlocked; nav loop bounded + cites sources.
- CLI verified end-to-end: `health`, `tools`, `integrations`, `chat` (mock LLM fallback), `audit`.
- Env probe: local box has Python 3.14 / Node 25 / Bun; NO Postgres/Ollama/Docker → core built
  to run anywhere; Postgres+Ollama+live creds switch on via config on the Ubuntu server.

### Phase 2 — Push 2: real data + MCP seam  ✅ (2026-06-01)
- `execution/clients/`: ported Kaseya/Cylance/Huntress clients, read-only, **stdlib urllib**
  (no requests/httpx dep), **injectable transport** for testing. Cylance JWT now via tested
  `encode_jwt_hs256` (stdlib, byte-exact vs jwt.io vector) instead of hand-rolled. ClientFactory
  + `credentials.require()` wired into `ToolContext.client()` (tenant-scoped, cached). `probe()` per client.
- Real read-only skills: kaseya_list_assets, kaseya_get_asset, cylance_list_devices,
  cylance_list_threats, huntress_list_agents, huntress_list_incidents (8 tools total w/ demos).
- **MCP server** (`execution/mcp_server.py`): dependency-free JSON-RPC/stdio; exposes the registry
  (initialize/tools/list/tools/call/ping); every call goes through dispatch(); **bound to one tenant**
  (args can't override it) = the fence for Hermes (D-12).
- CLI: added `probe` (Phase-L handshake) — verified fail-closed w/o creds; verified MCP over real stdio.
- **Tests: 56/56 pass** (added test_clients incl. JWT vector, test_skills_integration w/ fake clients,
  test_mcp incl. tenant-can't-be-overridden).

### Phase 3 — Push 3: dashboard + web API + Capability Console UI  ✅ (2026-06-01)
- Stack decision (logged): web layer is **stdlib http.server + self-contained Tailwind dashboard**
  (no FastAPI/React/build step) — runs identically dev + Ubuntu, zero new deps, hard to break on edit.
- `execution/web/`: auth.py (stdlib PBKDF2 + HMAC-signed sessions w/ TTL + single-admin bootstrap),
  api.py (pure testable router: login/logout, tools, integrations(+probe), capabilities GET/POST,
  audit, chat — all /api gated by session, fail-closed), server.py (ThreadingHTTPServer, cookies,
  serves SPA), __main__.py. Run: `python3 -m execution.web` → 127.0.0.1:8088.
- `dashboard/index.html`: self-contained SPA (Tailwind CDN, dark Hermes-style): login, sidebar,
  Chat (read-only badge, client selector, tool-event + citation chips), Integrations (status tiles +
  Test-connection probe), **Capability Console** (per-tool enabled/allow_write/require_approval toggles
  — the owner's throttle), Audit table.
- **Tests: 64/64 pass** (added test_api: auth-required, login, capability-edit-persists, chat, session-
  expiry). Live smoke-tested with curl (login→cookie→tools→caps edit→chat→integrations) AND visually
  via preview (login, Capability Console, chat shell screenshots).
- Dev: `.claude/launch.json` (preview server). Seed admin via DTM_ADMIN_PASSWORD or first-run prints one.

### Phase 2 — Memory + Knowledge vault  ✅ (2026-06-01)
- `execution/core/memory.py` (VaultStore): markdown vault (Obsidian-style), path `DTM_VAULT_PATH`.
  `kb/` knowledge base + `clients/<tenant>/memory.md` per-client notebook. Path-traversal-safe.
- Skills: `kb_search` (read, term-match + ranked snippets), `memory_read` (read), `memory_note`
  (internal write). **Internal-write rule:** dtm_ai-source writes touch only our vault (not client
  systems) → seeded `allow_write=True, require_approval=False` in build_agent, shown+toggleable in Console.
- SOP: `architecture/memory-vault.md` (first A-layer SOP). `vault/` gitignored (client data).
- **Tests: 71/71** (added test_memory: kb search/rank, memory roundtrip, wildcard refusal, path-safety,
  skills via dispatch). Demoed: kb_search finds a SonicWall runbook; memory_note→memory_read persists.
- 11 tools total now.

### Deploy decision (D-14, DEFERRED — do NOT touch live repo/server until owner says "deploy")
- Reuse `trinichad/KaseyaLink` repo, rename → DTM-AI (GitHub redirects so server's clone keeps pulling),
  tag last old commit `v0-kaseya-link`. One-time server migration (entrypoint + .env key remap), then
  `git pull && restart`. `gh` authed as trinichad. This is a Phase-T cutover, done on request only.

### Phase 2 — Hermes wiring kit  ✅ (2026-06-01)
- Read Nous Hermes Agent docs (MCP config ref, install, Ollama provider). MCP config = `~/.hermes/
  config.yaml` under `mcp_servers:` (command/args/env/enabled/timeout/tools.include); tools namespaced
  `mcp_<server>_<tool>`; reload via `/reload-mcp`; no `cwd` key (→ use launcher/PYTHONPATH).
- Verified `execution/mcp_server.py` launches cwd-independently (from /tmp via PYTHONPATH) + via wrapper.
- Deploy kit `deploy/hermes/`: `dtm-ai-mcp.sh` (launcher, chmod +x), `config.snippet.yaml` (per-client
  `dtm_<client>` mcp_servers entries, tools.include whitelist), `SETUP_HERMES.md` (step-by-step Ubuntu:
  install → local Ollama → register MCP → fence native toolsets → verify), `hermes-toolset-posture.md`
  (risk matrix; terminal/code/file/browser start OFF; ramp-to-autonomy order).
- SOP `architecture/hermes-integration.md`. Topology: ONE MCP server process per client (tenant-bound)
  = isolation at the Hermes layer; every Hermes call → dispatch() guardrails (actor=hermes).
- Two control planes documented: DTM AI Console (MSP/client tools) vs Hermes `tools`/MCP include (native).
- Tests still 71/71. (Standing Hermes UP is owner's step on the Ubuntu box — can't run Hermes from dev.)

### Phase 2 — Scoped read connectors / skill model (D-15)  ✅ (2026-06-01)
- Owner direction: NO hand-coded tools; all capabilities = LEARNED SKILLS (Hermes) composed from a
  small trusted set of guarded PRIMITIVES. Human control at the primitive layer (Console), not per skill.
- Built scoped generic read connectors: `clients/scopes.py` (per-vendor read-path allowlist,
  boundary-aware match, blocks auth/host-escape/out-of-scope) + skills `kaseya_read`/`cylance_read`/
  `huntress_read` (arbitrary allow-listed GET path → compose any read with zero new code).
- Writes stay SEPARATE individually-gated primitives. SOP: `architecture/skill-model.md`. D-4 reframed.
- Hermes config snippet updated to include the read connectors. **14 tools now.**
- **Tests: 80/80** (added test_scopes: allowlist allow/deny, auth blocked, host-escape/traversal blocked,
  boundary match, blocked-path-never-calls-client via dispatch).

### UI polish  ✅ (2026-06-01)
- Rebuilt `dashboard/index.html` (still self-contained: Tailwind + Lucide CDN, no build step):
  glass panels, gradient accents, Inter font, icon sidebar w/ active gradient bar + "system online"
  pulse. New **Overview** page (gradient stat cards: capabilities/integrations/read-only/write-enabled,
  recent-activity feed w/ pass-fail icons, integration-health panel). Integrations = cards w/ SVG status
  rings + styled probe. Capabilities = cards w/ category chips + risk dots + real gradient toggle
  switches (enabled/write/approval). Chat = avatars + bubbles + suggested-prompt chips + typing dots +
  provider footer. Toasts on capability changes. Verified all views via preview screenshots @1340px.

### Secure credential entry from the UI  ✅ (2026-06-01)
- `core/secrets_store.py` (SecretStore): app-managed `secrets.local`, 0600, atomic write, refuses
  world-readable, key allowlist enforced by caller. Wired into `config.Config` precedence
  (process env > SecretStore > .env > default).
- `credentials.set_integration(name, values)`: allowlist = that integration's spec keys only (no
  arbitrary-key injection); returns fingerprint-only status. `ClientFactory.invalidate()` so new
  creds take effect immediately.
- API: `GET /api/integrations/<n>/fields` (which keys set + fingerprints), `POST .../credentials`
  (admin, audited as credential_set with KEY NAMES only). Raw values never returned.
- UI: Integrations card → "Manage keys" → password fields per key (placeholder shows fingerprint),
  Save. Verified end-to-end via preview: entered Huntress key/secret → card went green/configured
  showing fingerprints (727be58/2b603a8), raw values appear in 0 API responses, secrets.local is
  -rw------- and gitignored, audit logged keys-only.
- SOP: `architecture/secrets.md` (encryption-at-rest via keyring/SOPS is the documented upgrade path).
- **Tests: 88/88** (added test_secrets: 0600, allowlist, clear, world-readable refusal, set roundtrip
  fingerprint-only, foreign-key/unknown-integration rejection, partial→complete).

### Skills page — view Hermes' learned skills  ✅ (2026-06-01)
- `core/hermes_skills.py` (HermesSkillsReader): walks `~/.hermes/skills/` (or DTM_HERMES_SKILLS_DIR),
  parses each `SKILL.md` frontmatter (name/description/category), tolerant of missing dir. Read-only.
- API `GET /api/skills` → {available, dir, skills}. Server flag `--hermes-skills-dir`.
- Dashboard: new **Skills** nav item + view — grouped by category, skill cards; clean empty state
  ("No learned skills yet…") + setup pointer until Hermes runs. Distinction made explicit in UI:
  Capabilities = primitives the AI MAY use; Skills = what Hermes has LEARNED (compositions).
- `examples/hermes-skills/` (3 sample SKILL.md) + dev preview points there via launch.json so the page
  renders populated; real server uses ~/.hermes/skills.
- **Tests: 92/92** (added test_hermes_skills: missing-dir clean, parse w/ category, frontmatterless
  fallback, example dir loads). Verified populated view via preview screenshot.

### Dashboard: integrations expansion, Memory tab, static sidebar, user accounts  ✅ (2026-06-01)
- **Sidebar static**: `#app` is now `h-screen overflow-hidden`; only the content `#view` scrolls.
- **Hermes + Obsidian as integrations**: `/api/integrations` now returns `kind: api|local`; UI shows an
  "API integrations" row (Kaseya/Cylance/Huntress + Manage keys) and a "Knowledge & agent" row
  (Obsidian Vault: KB/notebook counts; Hermes Agent: learned-skill count). Local tiles = status only.
- **Memory tab** (`brain` icon): `GET /api/memory?tenant=` → per-client `memory.md` text + KB doc list +
  client-notebook chips (click to switch tenant). `VaultStore.list_kb()/list_client_memories()`.
- **User accounts**: `auth.py` gains email column (+ migration) and CRUD (create/update/delete, role
  admin|user, guards: can't delete/demote last admin). API: `/api/me` (role+email), `/api/me/password`
  (self-service, verifies current), `/api/users` GET/POST, `/api/users/<n>` POST, DELETE (admin-only,
  audited; deleted-user sessions rejected). Server gained `do_DELETE`. UI: sidebar user card → Settings
  (My account password change + admin Users table: create/edit/delete).
- **Tests: 103/103** (added test_users). Live-verified all endpoints incl. DELETE + 403 gating + the UI
  via preview (integrations, memory, settings screenshots).

### LLM providers + model switching  ✅ (2026-06-01)
- Rewrote `core/router.py`: providers speak a NEUTRAL message history, each translates to its wire
  format. Implemented OllamaProvider (local /api/chat), **OpenAIProvider** (/v1/chat/completions, also
  any OpenAI-compatible endpoint), **ClaudeProvider** (Anthropic Messages API w/ tool_use↔tool_result
  folding) — previously a stub. All take injectable transport (unit-tested w/o network). MODEL_CATALOG +
  `available_models()` (local always; cloud appears when key set + DTM_ALLOW_CLOUD≠0) + `resolve(model_id)`.
- `agent.py`: ID-aware tool-call loop (cloud providers need tool_call_id pairing); `chat(model_id=...)`.
- LLM providers are now credential specs (group="llm"): **Anthropic/OpenAI key entry reuses the secure
  credential form**. `/api/models` endpoint; chat passes selected `model`. Selecting a non-ollama model =
  cloud opt-in (sets allow_cloud).
- UI: chat **model selector** dropdown (local + cloud, ☁ marks cloud); Integrations gains an **AI models**
  section (Local Ollama always-on, Claude/OpenAI Manage-key tiles).
- **Tests: 106/106** (added test_providers: OpenAI+Claude translation & tool-use, routing/availability;
  removed obsolete test_router). Live-verified: no key→only local; add Claude key via form→3 Claude models
  appear in selector + AI-models section shows fingerprint.

### Write-action approval workflow  ✅ (2026-06-02)
- `core/approvals.py` (ApprovalStore): proposed-action records (tool+exact args+tenant+actor), status
  pending|approved|rejected|executed|failed; one-shot via `claim_for_execution` (atomic pending→approved).
- `core/gates.py`: ConfigurableApprovalGate gains `needs_approval` + DEFERS approval-needed writes to the
  workflow in prod (never inline); `AlwaysApprove` gate executes an approved action once.
- `core/dispatch.py`: when a write needs approval and an ApprovalStore is wired, it CREATES a pending
  request and returns `{ok:false, status:"pending_approval", approval_id}` instead of executing.
- runtime wires ApprovalStore into the gate + `agent.approvals`; agent/mcp_server pass it to dispatch.
- API: `GET /api/approvals`, `POST /api/approvals/<id>/approve` (admin → executes args-bound via
  AlwaysApprove → mark_result), `/reject`; `/api/me` returns `pending_approvals` count.
- UI: **Approvals** nav item w/ pending badge; review cards (tool, args JSON, requester) + Approve &amp; run
  / Reject (admin); empty state; recent-decisions list.
- **Tests: 112/112** (added test_approvals: pending-not-executed, approve-executes-exact-args, reject,
  one-shot 409, non-admin 403, trusted-skips). Live-verified full chain: pending→approve→vault written,
  audit = approval_requested→tool_call→approval_executed, badge clears.

### Build tab — gated self-development agent (D-8)  ✅ (2026-06-02)
- `core/builder.py`: draft(description, router) → LLM writes a candidate skill → `skills_candidate/`
  (sandbox, NOT importable live) → `validate_candidate()` (AST security scan + schema lint) →
  human `promote()` (re-validate → copy to execution/skills/ → registry.discover()) / `reject()`.
- **Validator (safety-critical):** AST-based, fail-closed. Blocks os/subprocess/socket/etc imports,
  eval/exec/open/getattr, dunder access, non-read CATEGORY, and ANY top-level statement (no import-time
  exec). Promoted tools start CATEGORY=read + ENABLED_BY_DEFAULT=False (zero blast radius until enabled).
- API (admin-only): POST /api/build/draft, GET /api/build/candidates, POST .../promote, .../reject.
- UI: **Build** nav (admin-only) — describe a tool → drafted code + validation (green/issues) →
  Promote/Discard; candidate list.
- SOP: `architecture/self-development.md`. `skills_candidate/*.py` gitignored.
- **Tests: 122/122** (added test_builder: validator accepts good / blocks os+exec+write+toplevel-call+
  missing-attrs+syntax; draft→stage→promote→reject sandbox via FakeRouter). Live-verified: staged
  candidate → Promote → 15 tools, new tool live but DISABLED, candidate consumed; demo artifact cleaned.

### Next (continue building)
- Harden Build: run the test suite against a candidate in an isolated worktree before promote.
- Offline asset vendoring (Tailwind/Lucide/fonts) for air-gapped use. Deploy cutover on owner's go.
- More read-only tools / scoped-connector coverage as new vendors are added.
- Deploy cutover (on owner's "deploy" go — D-14). Then `deploy/hermes/SETUP_HERMES.md` to stand up Hermes.
- New vendors (M365/Google/Datto/…): add creds + a scoped connector each → unlimited learned reads on top.

### Deploy packaging + offline asset vendoring  ✅ (2026-06-02)
- Deploy artifacts: `deploy/dtm-ai.service` (hardened systemd), `deploy/nginx-dtm-ai.conf`,
  `deploy/SETUP.md` (from-zero Ubuntu), top-level `README.md`. Updates = `git pull && systemctl restart`.
- **Offline vendoring**: vendored Tailwind Play CDN (407KB) + Lucide UMD (402KB) + Inter woff2 (400–800,
  latin) into `dashboard/vendor/` + `vendor/fonts.css`. index.html → `/vendor/*` (zero CDN refs). server.py
  serves `/vendor/` (content-types + Cache-Control) with a traversal guard (encoded-traversal → 404, no
  source leak — verified). Dashboard renders fully offline (tailwind+lucide+Inter all local).

### Errors / tests
- All green (122 tests). ResourceWarning (unclosed sqlite) fixed by adding AuditStore.close().

---

## 2026-06-03 — DEPLOYED & LIVE on the Ubuntu box (ross@192.168.5.60:/opt/dtm-ai, svc on :8090)
Deploy cutover happened (D-14 done): repo `github.com/trinichad/DTM-AI`, systemd `dtm-ai` as user
`dtm-ai`. Maintainer access: SSH key + scoped `/etc/sudoers.d/dtm-ai-ross` (ross→dtm-ai + manage the
service, no broad root). Update flow = `sudo -u dtm-ai git -C /opt/dtm-ai pull && sudo systemctl restart
dtm-ai`. (NOTE: server reachable only when the maintainer Mac is on the 192.168.5.x LAN.)

### Real vendor data verified live + integration fixes  ✅
- **Kaseya** auth corrected to the proven **VSA 9.5** scheme (Basic user/pass → /api/v1.0/auth → Bearer);
  the VSA-X token-id/secret path never authenticated. Probe uses `/assetmgmt/assets?$top=1` (read-only
  account lacks `/system/orgs`). Stale `KASEYA_TOKEN` in SecretStore had to be force-cleared.
- **Cylance device count saga** (10000→1800→200→**1707 correct**), proven against the live API:
  (1) pagination ran to the 50-page cap → stop on `total_pages`; (2) full-but-identical pages →
  the real bug: **Cylance's request param is `page`, not `page_number`** (it only *echoes* page_number),
  so every page returned page 1; (3) dedup by `id` as a safety net. `total_number_of_items=1707` matches
  full enumeration. Lessons in `architecture/skill-model.md`. **Huntress 1920 / Kaseya counts NOT yet
  re-verified the same way — still open.**
- `core/sysstats.py`: host CPU/mem/disk/GPU on Overview (`/api/system/stats`). Model-aware + tunable
  context window; fleet counts cached stale-while-revalidate (`/api/fleet`, TTL 300s).

### Server-side persistent chat history (multi-conversation)  ✅
- `core/conversations.py` (ConversationStore, SQLite, owner-scoped fail-closed): create/list/get/rename/
  delete/compact + auto-title. Chat is now server-authoritative (browser no longer holds transcripts),
  per-user-private, tenant-bound. API: `/api/conversations` CRUD + `/compact`; `/api/chat` persists turns.
  UI: two-column chat (conversation rail: new/switch/rename/delete) — delete icons always visible + header
  Delete. Old browser `localStorage` chats NOT migrated (by design). SOP `architecture/conversations.md`.

### AuthStore concurrency fix  ✅
- `AuthStore` shared one sqlite connection across the threaded server with NO lock → intermittent
  `sqlite3.InterfaceError: bad parameter or other API misuse` on the auth path (caught in live logs).
  Added a `threading.Lock` (mirrors AuditStore/ConversationStore); update_user uses locked-internal
  helpers to avoid re-entrancy. Verified with a 12-thread×400 stress (0 errors).

### Streaming chat (SSE) — PUSHED, NOT YET DEPLOYED  ⏳ (commit 09c67e8)
- Token-by-token answers + live tool-event chips. Transport = **SSE** over the stdlib server
  (`POST /api/chat/stream`), chosen over WebSocket — **D-16** in decisions.md. `clients/_http.http_stream`
  (urllib line iterator); provider `chat_stream` real streaming for **Ollama (NDJSON)** + **Claude
  (Anthropic SSE)**, OpenAI/Mock emit whole (later refinement). `agent.chat_stream` emits
  tool_call/tool_result/delta; `Api.stream_chat` bridges push→pull via queue+thread, same persistence.
  Frontend reads frames via `fetch().body.getReader()`, settles to the formatted answer.
- **PENDING:** deploy (`git pull && restart`) + live verify that Ollama actually streams tokens through
  nginx (the `X-Accel-Buffering: no` header should prevent buffering). Lost SSH mid-session (Mac left the
  LAN); owner has no SSH today. Do this first next session.

### Tests
- **157 green** (was 122): added test_conversations, streaming tests in test_providers/test_agent/
  test_api, Cylance pagination regressions, sysstats, context/fleet, dedup.

### Open / next (refreshed)
1. **Deploy + verify streaming** (09c67e8) — FIRST next session when the box is reachable.
2. Verify Huntress (1920) & Kaseya counts via live probe like Cylance; confirm Cylance/Huntress green.
3. **Microsoft 365 / Entra** read-only (next integration): users, MFA audit, inactive, Intune.
4. Encrypt secrets at rest (`secrets.local` is plaintext 0600 today) — keyring/age-SOPS.
5. Refine OpenAI streaming (token + tool-call deltas); optional: stream over true WS for alerts.
6. Scheduled audits/reports (cron installer); on-demand report framework.
7. Later vendors (Google/Proofpoint/Datto-Veeam/SonicWall/…); Postgres+RLS for prod scale; stand up Hermes.

## 2026-06-09 — Remove Hermes, go fully in-house (D-19) ✅
Owner chose a wholly in-house build (no external runtime). Inventory showed the native brain already
existed (`agent.py` loop) with Hermes only an alternate engine + delegation path. Executed in 6 tested
phases — each committed + pushed to `main`; Phases 1–5 + the frontend tidy deployed live (8090 healthy):
- **P1 Native profiles** — `hermes_agents.py`→`core/agents.py`; profiles DTM-AI-owned (`DTM_AGENTS_DIR`,
  legacy `DTM_HERMES_*` fallback). Verified live: 8 agents (AtlasOps + 7 specialists) load.
- **P2 Profile-aware loop** — `build_system_prompt(profile)` layers SOUL+memory below the safety base;
  `chat`/`chat_stream` take `profile=`; per-profile chat now possible.
- **P3 Native delegation** — `core/tasks.py` `TaskStore`+`Dispatcher` (a worker runs the loop AS the
  profile); DELETED `hermes_kanban.py` + the root-owned sudo wrapper + sudoers + installer. Verified live.
- **P4 Learning skills** — `core/playbooks.py` (dedup'd markdown playbooks) + `skill_search` tool +
  `suggest_skill` on chat answers + `/api/skills/learn|/<slug>`. Owner-confirmed saves only (I-5).
- **P5 Strip Hermes** — deleted `hermes_bridge`, `mcp_server`, `hermes_brain`, `hermes_skills` + 4 tests;
  removed the `engine=='hermes'` branch, `_stream_hermes`, brain routes, Hermes integration card. 205
  tests green. Retired the `dtm-ai-mcp` service on the box.
- **P6 Dashboard + docs** — removed inert Hermes UI (engine/brain toggles), wired the "save as skill?"
  prompt + native Skills view; updated CLAUDE.md §7b/I-5; SOP → `architecture/agents-delegation-skills.md`.
- **Also retired the old Kaseya AI Link** (`kaseya-ai.service`, port 8088) — stopped + disabled.

### Open / next
1. **Migrate profiles** off `/srv/hermes-data` to a DTM-owned `DTM_AGENTS_DIR`, then drop the
   `hermes-rw.conf` drop-in + the legacy env fallback.
2. **Stop/remove the Hermes Docker container** on the box (nothing routes to it now).
3. Optional: delete `/opt/kaseya-ai` (71 MB, owner's call).
4. Delegation-worker quality: confirm the local model handles tool-calls well, or add a per-task cloud opt-in.
5. (carried) M365/Entra read-only; encrypt secrets at rest; scheduled audits/reports.
