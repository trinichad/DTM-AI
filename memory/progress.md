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

### Next
- Wire Hermes Agent to the MCP server (on Ubuntu, with models) + add its toolsets to the Console.
- Real approval-token workflow (replace present-token placeholder in gates.py).
- Deploy cutover (on request). Optional: upgrade dashboard to shadcn/React later.
- On the server: fill `.env` (Kaseya/Cylance/Huntress) → `python3 -m execution.cli probe` goes green.

### Errors / tests
- All green. ResourceWarning (unclosed sqlite) fixed by adding AuditStore.close().
