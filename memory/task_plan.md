# task_plan.md — DTM AI Build Plan

**North Star:** A secure, read-only conversational assistant the DTM team chats with to check things
across all client environments (devices, users, security posture, alerts) and get instant, sourced
answers — no write actions in v1.
**Phase-L green credentials (verifiable today):** Kaseya VSA, Cylance, Huntress. All others = read-only
stubs, wired in Phase 3 as creds become available (M365/Entra next).
**Status (2026-06-03):** BUILT & DEPLOYED — live on the Ubuntu box (`/opt/dtm-ai`, svc `:8090`), 157 tests
green. Phases 0–3 (core, agent toolkit, dashboard, web API, capability console, approvals, build agent,
memory/Hermes kit, 3 green vendors) done; vendor data verified live (Kaseya VSA9.5, Cylance=1707,
Huntress). Recent: server-side multi-conversation chat, AuthStore concurrency fix, **streaming chat
(SSE) pushed but NOT yet deployed (commit 09c67e8 — deploy first next session)**. See `progress.md`
(2026-06-03) for the authoritative current state + open list. Next big feature: Microsoft 365 / Entra.
_(This phased list below is the original blueprint; many items are now ✅ — progress.md is the live log.)_

## ► CURRENT FOCUS — Hermes brain on Docker (game plan, 2026-06-04)

Wire the **Nous Hermes Agent** in as the conversational brain, run inside **Docker** as the
security fence (owner has NO root on the Ubuntu box → can't make a dedicated powerless user;
Docker is the no-root fence — see **D-17**). Native tool lockdown via config proved insufficient
(`agent.disabled_toolsets` does NOT reach delegated sub-agent workers — a delegated worker still
ran a shell as `ross`, who can `sudo -u dtm-ai` to the creds). So the fence moves to the OS/container
layer, NOT the tool list. With the container fence, native tools (terminal/file/code/delegation) can
stay ON — the container, not the tool config, contains the blast radius.

> **Correction (2026-06-05):** the "no root" premise was inaccurate — `ross` is in the `sudo` group with
> `(ALL : ALL) ALL` (password-gated) + `(dtm-ai) NOPASSWD: ALL`. So the owner can self-grant docker (no
> admin) AND the powerless-user option was actually viable; owner reaffirmed **Docker**. Fence still
> required: the `(dtm-ai) NOPASSWD` line lets any process as `ross` reach client creds with no password.

**Game plan (in order):**
1. **Install Docker access** — owner self-grants: `sudo usermod -aG docker ross`, re-login, confirm
   `docker ps` works without sudo. Docker is already `enabled` on boot. _(no admin needed)_
2. **Install Hermes** inside a container. Data dir on a HOST volume `/srv/hermes-data` mounted to the
   container's `~/.hermes` (config, SOUL.md, skills/, memories/, profiles). Creds (`/opt/dtm-ai`)
   are NOT mounted. Run with `--restart unless-stopped`.
3. **Connect to OpenAI** — `hermes model` → "Sign in with ChatGPT" (Codex OAuth device flow; rides the
   owner's ChatGPT Plus, NO API key). Re-auth fresh inside the container.
4. **Connect to DTM AI** — the two channels:
   - OUT: Hermes → DTM AI tools via the **MCP server over HTTP**. ✅ **BUILT (2026-06-05)** —
     `execution/mcp_server.py --transport http --host <bridge> --port 8089`; tenant bound by URL path
     (`/mcp/<client>`, fence preserved); `DTM_MCP_TOKEN` bearer auth optional; GET `/health` open.
     Tested (14 MCP tests incl. real HTTP loopback; full suite 174 green). Container `url:` form in
     `deploy/hermes/config.snippet.yaml`; SOP `architecture/hermes-integration.md` updated.
   - IN: DTM AI chat → Hermes brain (publish a Hermes API port to localhost) so "chatting in DTM AI
     talks through Hermes." This is a real build (DTM AI↔Hermes bridge + streaming), not a flag. _(TODO)_

**Integration goals (all achievable with the /srv volume — container fences execution, NOT data):**
- Hermes' **skills show in DTM AI's skills tab**, viewable in UI (point `DTM_HERMES_SKILLS_DIR` at
  `/srv/hermes-data/skills`; `/srv` is NOT masked by the service's `ProtectHome`, unlike `/home` —
  this also fixes the old skills-pane problem).
- **View + edit profiles / SOUL / memory in the DTM AI UI** (they're plain files on the host volume).
- **Memory/SOUL are model-agnostic** (markdown files; work whether Hermes drives OpenAI or local LLM).
- **Manager + specialist profiles** (office / security / backup / kaseya) visible/editable in UI.

**Status (2026-06-05):** Steps 1, 2, and 4-OUT DONE. Hermes is **installed in Docker and visible to DTM AI.**
- **Step 1** ✅ `ross` in `docker` group, `docker ps` works, docker `enabled` on boot. (Self-granted — `ross`
  has full password'd sudo; the "no root" premise was wrong, see D-17 correction.)
- **Step 2** ✅ Upstream official compose (`~/hermes-agent`, image `hermes-agent:latest` 1.09GB,
  `gateway`+`dashboard` services, `restart: unless-stopped`, `network_mode: host`). Volume repointed
  `~/.hermes` → **`/srv/hermes-data`** (host). `HERMES_UID/GID=994/981` (= `dtm-ai`) via repo `.env` so
  Hermes writes its data **owned by `dtm-ai`** → the DTM AI service reads/edits it natively (no shared-group
  gymnastics; fence intact — only `/srv/hermes-data` is mounted, never `/opt/dtm-ai`). Container healthy,
  `default` profile registered, 18 skill categories / **74 SKILL.md** seeded.
- **DTM AI visibility** ✅ `DTM_HERMES_SKILLS_DIR=/srv/hermes-data/skills` added to `/opt/dtm-ai/.env`
  (still 0600 dtm-ai:dtm-ai), service restarted. Deployed `HermesSkillsReader` returns **74 skills,
  available=True** → Skills tab + integrations panel show "Hermes Agent — 74 learned skills".
- **Step 4-OUT** ✅ MCP HTTP transport (built earlier this session). With `network_mode: host` the container
  reaches the host MCP at `127.0.0.1:8089` directly (no bridge/host-gateway needed).

- **Step 3** ✅ (2026-06-05) OpenAI connected via Codex OAuth — `hermes setup` (full) done inside the
  container. Model `gpt-5.5`, provider `openai-codex` (base `chatgpt.com/backend-api/codex`, owner's
  ChatGPT Plus, no API key). `auth.json` present (0600 dtm-ai). Terminal backend kept `local` (= inside
  the container = contained). Native tools enabled (terminal/file/code/skills/vision/TTS/browser-local);
  Computer-Use(macOS) + image-gen trimmed. Web premium search skipped (free DuckDuckGo skill covers it);
  no GITHUB_TOKEN (Hub installs off; 74 built-ins present). Config/.env/auth.json all owned by `dtm-ai`.

- **OUT channel** ✅ **WIRED & VERIFIED (2026-06-05).** Hermes → DTM AI guarded tools over MCP-HTTP.
  - MCP server runs on host as `dtm-ai`, `127.0.0.1:8089`, Bearer-gated (`DTM_MCP_TOKEN`, 64-hex, in
    `/opt/dtm-ai/.env`). Verified: health 200, no-token 401, authed `system_health` ok tenant=`*`.
  - Container reaches it via host loopback (`network_mode: host`) — confirmed `200` from inside.
  - Hermes config: `mcp_servers.dtm_all` → `http://127.0.0.1:8089/mcp` + Bearer header, in
    `/srv/hermes-data/config.yaml` (0600). `hermes mcp test dtm_all` → ✓ connected 28ms, **16 tools**
    discovered (kaseya/cylance/huntress reads, endpoint_coverage, kb_search, memory, system_health).
  - **End-to-end proof:** `hermes -z` agent run on gpt-5.5 called `system_health` through the fence;
    DTM AI audit shows `hermes | system_health | read | ok=1`. Every call still goes through dispatch().
  - **Persistence:** systemd unit `deploy/dtm-ai-mcp.service` committed (bb517ca); install/enable needs
    one root step (owner has only password'd sudo). Until enabled, the MCP server is NOT running (manual
    test process was stopped to free the port). Cmd: `sudo cp /opt/dtm-ai/deploy/dtm-ai-mcp.service
    /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now dtm-ai-mcp`.

- **IN channel** ✅ **WIRED & VERIFIED (2026-06-05).** DTM AI dashboard chat can talk THROUGH Hermes.
  - Enabled Hermes' OpenAI-compatible **api_server** (gateway platform): `API_SERVER_HOST=127.0.0.1`,
    `API_SERVER_PORT=8642`, `API_SERVER_KEY` (in `~/hermes-agent/.env` + compose env; same key mirrored
    to `/opt/dtm-ai/.env` as `HERMES_API_KEY`). `network_mode: host` → dtm-ai backend reaches it on
    loopback (the backend is NOT in the docker group, so a network relay, not docker-exec).
  - `core/hermes_bridge.py`: relays a turn to `POST /v1/chat/completions` (Bearer, `X-Hermes-Session-Id`
    = conversation_id for continuity). Parses content deltas AND `event: hermes.tool.progress` frames
    into the UI's existing frame shapes (tool_call/tool_result/delta). Strips `mcp_dtm_<client>_` naming.
  - `web/api.py`: `engine` field ("hermes" | "dtm") on `_chat` + `stream_chat`; `_stream_hermes` worker.
    Hermes integration card now shows brain (`gpt-5.5 / OpenAI Codex`) + `chat_engine` flag — DISTINCT
    from DTM AI's own OpenAI/Claude keys (separate connection; do NOT conflate).
  - Dashboard: **engine selector** (Hermes·<brain> | DTM AI direct), defaults to Hermes when available;
    model picker disabled when Hermes drives; `engine` sent per turn. JS node --check OK; 181 tests green.
  - **End-to-end proof:** bridge.stream() as dtm-ai → tool_call/tool_result(system_health) + deltas +
    "DTM AI is OK."; audit shows `hermes | system_health | ok=1` (full chain: chat→Hermes→MCP→dispatch).
  - Persistence: api_server rides the `gateway` container (`restart: unless-stopped`, docker enabled on
    boot). MCP server = systemd `dtm-ai-mcp` (enabled). All survive reboot.

**Skills pruned (2026-06-05):** trimmed Hermes' 74 stock skills → **10** kept for MSP ops: obsidian,
hermes-agent-skill-authoring, plan, systematic-debugging, ocr-and-documents, google-workspace,
teams-meeting-pipeline, architecture-diagram, jupyter-live-kernel, himalaya. Removed the creative/ML/
coding/apple(macOS-only)/jailbreak noise. Full original set backed up (reversible):
`/srv/hermes-data/.skills-backup-20260605-150859.tar.gz`. DTM AI reader reflects live (no GITHUB_TOKEN →
Hub won't silently re-add them; a `hermes update` could re-seed built-ins — re-prune if so).

**Transcript + privacy tuning (2026-06-05):**
- **Inline tool data** ✅ — each tool pill shows an expandable "returned data" panel. Hermes path: MCP
  `_call` writes a capped, 30-min-TTL preview to `audit.tool_result_cache`; `_stream_hermes` correlates
  by actor=hermes + turn-start window. Direct engine: preview attached in-process. (Needed a `dtm-ai-mcp`
  restart to activate — now passwordless via the installed sudoers snippet.)
- **Hermes local-model** — CORRECTED. Per-request model override does NOT work: Hermes' api_server is
  single-model (`_create_agent` → `_resolve_gateway_model()` reads `config.yaml` per request; the request
  `model` field is cosmetic, echoed only). Proven by polling Ollama: a "local" per-request turn never
  loaded the 27B model — cloud served it. **Removed that misleading dropdown option.** ✅ **Real fix =
  brain swap** (`core/hermes_brain.py`): rewrite the `model:` block cloud↔local. Config read per-request
  → swap is **live, no restart**; Codex token in separate `auth.json` → **no gpt re-auth**. Global,
  owner-gated, audited toggle in the chat header ("brain: ☁ cloud / 🔒 local · switch"). Local =
  `qwen3.5:27b` via `provider: custom` → Ollama `/v1` (262K ctx). Verified live: swap to local loaded
  qwen in Ollama; swap back restored gpt-5.5 with no re-login; other config keys preserved.
  Needs drop-in `deploy/dtm-ai.service.d/hermes-rw.conf` (ReadWritePaths=/srv/hermes-data) — **installed**.
  Genuinely-local alternative also exists: DTM-direct engine on `ollama:qwen3.5:27b` (router truly hits
  Ollama — verified). 187 tests green.
- `dtm-ai-mcp` now managed passwordless by ross (`/etc/sudoers.d/dtm-ai-mcp-ross`, 0440). NOTE: the older
  `/etc/sudoers.d/dtm-ai-ross` has loose perms (visudo warns "should be 0440") — works today; tighten with
  `sudo chmod 0440 /etc/sudoers.d/dtm-ai-ross` to avoid a stricter sudo ignoring it later.

**Remaining / optional:**
- Use it: open the dashboard → Chat; the engine selector defaults to **Hermes** — ask a client question.
- Optional: per-client `mcp_servers` entries (`dtm_<client>` → `/mcp/<tenant>`) beyond `dtm_all`; sudoers
  snippet `deploy/sudoers-dtm-ai-mcp-ross.snippet` (passwordless `dtm-ai-mcp` mgmt for ross) — committed,
  install optional. Richer IN-channel: surface Hermes per-tool detail / citations in the DTM AI transcript.

**Known cosmetic items (non-blocking):** (a) host dir group shows 10000 not 981 — irrelevant, OWNER is
`dtm-ai`; (b) the UID remap re-chowns the baked-in `/opt/hermes` build trees on EVERY container start
(slow start, ~minutes) — acceptable for a rarely-restarted service; revisit only if restarts get painful.

## Phase 0 — Initialization  ✅ in progress
- [x] Recon of `Kaseya Link` + `ClaudeOS [Hermes] V2` (reuse/replace matrix → findings.md)
- [x] Lock architecture forks (tenancy, model posture, self-coding gate, UI hosting → decisions.md)
- [x] Scaffold `/memory /architecture /execution /.tmp`
- [ ] Capture North Star + Phase-L credential inventory (final 2 questions)
- [ ] Finalize CLAUDE.md as Project Constitution (data schema, behavioral rules, invariants)
- [ ] Get user sign-off on this plan

## Phase 1 — Foundation
- [ ] Clean repo structure + git init + `.gitignore` (never commit `.env`, `live-data.json`, db)
- [ ] FastAPI backend skeleton; config loader (SOPS/keyring + 0600 + fingerprints)
- [ ] Postgres schema: `tenants, users, audit_log, tool_registry, tool_config, alerts, tasks, approvals`
      + row-level security on `tenant_id`
- [ ] **Model router** abstraction: provider interface (Ollama local default, Claude/OpenAI opt-in),
      per-task routing by sensitivity/complexity/cost/speed
- [ ] Port the auto-discovery registry → `skills/`; add JSON-Schema arg validation
- [ ] Audit logging on every call (read incl., args hashed)
- [ ] Dashboard shell from Hermes donor (sidebar + header + Outlet, Tailwind/shadcn), chat over WebSocket
- [ ] Auth: sessions w/ TTL + rotation; admin-gated mutations
- [ ] **Verify:** local LLM round-trips a chat; one read-only sample tool runs end-to-end in the UI

## Phase 2 — Agent Toolkit + Brain
- [x] Tool permission/risk model enforced in `dispatch()` (read/alert/write/destructive)
- [x] **Capability Console backend** (`core/capabilities.py` + `core/gates.py`): per-tool
      enabled/allow_write/require_approval + safety floors; CLI `caps` / `caps-set`. (D-11)
- [ ] Approval workflow: write → proposed-action record → human approve → one-shot args-bound token
      (replaces the present-token placeholder in gates.py)
- [ ] **MCP server** exposing the registry, so Hermes / any MCP brain uses our guarded tools (D-12)
- [x] **Hermes Agent integration kit** (fenced, D-12): MCP server verified cwd-independent; deploy kit
      `deploy/hermes/` (launcher `dtm-ai-mcp.sh`, `config.snippet.yaml`, `SETUP_HERMES.md`,
      `hermes-toolset-posture.md`); SOP `architecture/hermes-integration.md`. Owner runs it on the
      Ubuntu box where Hermes + models live (can't run Hermes from dev). Native-toolset control stays in
      Hermes config (documented matrix); DTM AI Console governs the MSP tools.
- [x] **Memory + Obsidian** (D-13): VaultStore + `kb_search` (read), `memory_read` (read),
      `memory_note` (internal write, seeded-allowed/toggleable); path-safe; SOP `architecture/memory-vault.md`.
- [ ] Capability Console UI (the dashboard surface for enable/allow_write/require_approval + autonomy ramp)
- [ ] Report-generation framework (normalized snapshot → client-ready report)
- [ ] **Sandboxed coding agent** + `skills_candidate/` staging + promotion gate (lint/test/scan/merge)

## Phase 3 — MSP Integrations (read-only first)
- [ ] **Lead with the 3 green clients** (already built in Kaseya Link): port + harden Kaseya VSA,
      Cylance, Huntress clients through `credentials.require()`; per-client vault entries + health probes.
- [ ] Microsoft 365 / Entra read-only NEXT (device-code/OAuth, MFA-aware): users, MFA audit, mailbox
      deleg, inactive users, Intune devices, tenant config issues
- [ ] Then as creds arrive: Google Workspace, Proofpoint, Datto/Veeam backup status,
      SonicWall/Ubiquiti/Synology reporting

## Phase 4 — Automation
- [ ] Scheduled audits/reports/health-checks (cron installer pattern); alert sweeps; summaries

## Phase 5 — Controlled Write Actions
- [ ] Carefully approved write tools (M365 user/license/group), confirmation + full audit + rollback

## Testing strategy
- Unit tests per tool against fixtures (no live creds in CI); schema-lint on every tool; security scan
  (no shell-out, no undeclared egress, CATEGORY declared); integration probes gated behind real creds;
  every shipped output carries a one-line verify command (per constitution Phase S).

## Backup / rollback
- Everything under git; tool promotions are commits; config snapshots; disable-by-config kill switch;
  DB + `.env` backed up before major changes (per SETUP_GUIDE backup section).
