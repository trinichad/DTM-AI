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

### Next
- Approval workflow (one-shot args-bound tokens) → safely open WRITE primitives in the Console.
- Deploy cutover (on owner's "deploy" go — D-14). Then `deploy/hermes/SETUP_HERMES.md` to stand up Hermes.
- New vendors (M365/Google/Datto/…): add creds + a scoped connector each → unlimited learned reads on top.

### Errors / tests
- All green. ResourceWarning (unclosed sqlite) fixed by adding AuditStore.close().
