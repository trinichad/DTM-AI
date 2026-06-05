# SOP — Hermes Agent Integration (A-layer)

> A.N.T. golden rule: if this logic changes, update this SOP **before** the code.
> Implements D-12. Code: `execution/mcp_server.py`. Deploy kit: `deploy/hermes/`.

## Goal
Use **Nous Research Hermes Agent** (open-source Python autonomous agent: persistent memory,
self-improving skills, 20+ chat channels) as DTM AI's conversational brain — *without* giving an
autonomous, terminal-capable agent unguarded access to client environments.

## The fence (why this is safe)
Hermes reaches client systems ONLY through DTM AI's tool registry, exposed as an **MCP server**
(`execution/mcp_server.py`). Every Hermes tool call becomes a `dispatch()` call, so ALL guardrails
apply no matter how capable/autonomous Hermes is:
- read-only by default; write/destructive gated by the Capability Console + approval
- tenant isolation (server is **bound to one tenant**; any tenant in args is ignored)
- JSON-Schema arg validation; per-tool kill switch
- every call audited (actor = `hermes`)

```
Hermes (brain: memory, channels, reasoning)
   │  MCP (stdio JSON-RPC)   tools namespaced mcp_dtm_<client>_<tool>
   ▼
DtmMcpServer --tenant <client>   ← ONE process per client = tenant isolation
   │
   ▼  dispatch()  ← the immovable guardrail boundary
DTM AI tools → vendor clients (read-only) / vault
```

## Topology

Two transports, same JSON-RPC handler and the **same fence** — the tenant binding is what differs.

### A) stdio — Hermes launches the server (dev / same-host)
- **One MCP server process per client.** A `dtm_<client>` entry in `~/.hermes/config.yaml` launches
  `deploy/hermes/dtm-ai-mcp.sh --tenant <client>`. Hermes namespaces its tools `mcp_dtm_<client>_*`,
  so the brain physically cannot cross tenants through the tools.
- The launcher sets cwd; the server resolves `.env`, `dtm_ai.db`, and `vault/` from its own location
  (`__file__`), so it is cwd-independent (verified launching from `/tmp` via `PYTHONPATH`).

### B) HTTP — Hermes connects over the network (Docker fence, D-17)
When Hermes runs **inside a container** (the execution fence — see D-17), it cannot launch a host
process, so stdio is out. Instead the MCP server runs **on the host** (as `dtm-ai`, holding the creds)
and the container connects over HTTP:

```
python3 -m execution.mcp_server --transport http --host <bridge-ip> --port 8089
```

- **Tenant is bound by the URL PATH**, preserving the same per-tenant fence stdio gets from separate
  processes: `POST /mcp` → tenant `*`; `POST /mcp/<client>` → that client. A `tenant_id` smuggled in
  call args is still ignored — the path wins (tested: `test_url_path_is_the_fence`).
- **One process serves all tenants** (one shared agent); the path routes each request. Hermes config
  uses a `url:` entry per client instead of `command:` — e.g. `http://host.docker.internal:8089/mcp/acme`.
- **Auth:** set `DTM_MCP_TOKEN` in the host env → every POST must carry `Authorization: Bearer <token>`
  (GET `/health` stays open for liveness). The token rides the env, never the process list or config args.
- **Bind address:** bind the docker bridge / host-gateway IP (e.g. `172.17.0.1`), NOT `0.0.0.0` (that
  would expose creds-backed tools to the LAN) and NOT `127.0.0.1` (unreachable from the container unless
  `--network host`). The container reaches it via `host.docker.internal` (`--add-host=host.docker.internal:host-gateway`).
- **Creds never enter the container:** the server (and `/opt/dtm-ai`, `.env`) stay on the host; the
  container holds only Hermes' own data (`/srv/hermes-data`). That is the whole point of the fence.

## Two control planes (don't conflate)
| Plane | Controls | Where |
|---|---|---|
| **DTM AI Capability Console** | the MSP tools that touch client systems (enable / allow_write / require_approval) | DTM AI dashboard |
| **Hermes `tools` / MCP `tools.include`** | Hermes' own native toolsets (terminal/code/file/browser) + which MCP tools it sees | `hermes tools`, `~/.hermes/config.yaml` |

DTM AI's Console is the security boundary for *client* actions. Hermes' native dangerous toolsets are
fenced by keeping them OFF in the MSP profile (`deploy/hermes/hermes-toolset-posture.md`).

## Local-first
Point Hermes at the local Ollama OpenAI-compatible endpoint (`http://127.0.0.1:11434/v1`). DTM AI's
own model router independently enforces local-first for client-data tasks, so sensitivity is protected
at two layers.

## Brain swap — cloud ↔ local, live (D-12; `core/hermes_brain.py`)
Hermes' api_server is **single-model**: `_create_agent` builds the agent from
`_resolve_gateway_model()` (reads `config.yaml` model block) **per request** — the per-request
`model` field is only echoed in the response, it does NOT switch the LLM (verified: a per-request
"local" override never loaded Ollama; the cloud served it). So a per-chat brain switch is NOT possible
through one api_server. Instead we **swap the `model:` block in `config.yaml`** between two definitions:
- **cloud** → `default: gpt-5.5`, `provider: openai-codex`, Codex base_url
- **local** → `default: qwen3.5:27b`, `provider: custom`, `base_url: http://127.0.0.1:11434/v1`

Because config is read per request, the swap takes effect on the **next turn with no container restart**.
The Codex OAuth token lives in a **separate `auth.json`** that the swap never touches → **no gpt
re-login** when flipping back to cloud. This is a **GLOBAL** setting (one config), so it's surfaced as an
owner-gated, audited toggle — not a per-message dropdown (which would race + can't actually switch).

- API: `GET /api/hermes/brain` (mode/model), `POST` (owner-only, audited `config_change`).
- The web service must be able to WRITE the config dir → drop-in
  `deploy/dtm-ai.service.d/hermes-rw.conf` adds `ReadWritePaths=/srv/hermes-data` (ProtectSystem=strict
  makes it read-only otherwise). The MCP service does NOT need this (it only reads + caches to the DB).
- The Hermes engine label in the dashboard reflects the REAL configured brain (read from config), so a
  swap can't silently misreport which model is answering.

## Agent team — profiles as specialists (`core/hermes_agents.py`)
Each specialist agent **is a Hermes profile** on the shared volume: AtlasOps Manager = the `default`
profile (chat flows through it); specialists live under `profiles/<name>/` (`SOUL.md`, `config.yaml`,
`profile.yaml` description, `memories/`, `sessions/`, `skills/`). The Agents tab reads each one's soul,
role, brain (per-profile config), and how it's "compounded" (memory entries, skills, sessions).

**Add/delete is pure on-disk file IO — no `docker exec`.** Hermes discovers profiles by *scanning* the
`profiles/` dir (`hermes_cli/profiles.py list_profiles()` → `iterdir()`), so writing/removing the files
IS the create/delete. This matters because the web service (`dtm-ai` user) is **not in the docker group**
and cannot `docker exec`; it only needs RW to `/srv/hermes-data` (the same `hermes-rw.conf` drop-in used
by the brain swap). Verified on the box: a cloned profile dir appeared in `hermes profile list` and the
DTM AI reader picked it up immediately.
- `create_agent(name, soul, description, role)` — validates the name (`^[a-z0-9_-]+$`, not `default`,
  not existing), mkdir the profile + empty `memories/sessions/skills`, **copies the manager's
  `config.yaml`** so the new agent inherits the same MCP fence + tools + cloud brain (swap to local
  per-agent after), writes `SOUL.md` (a safe stub if none pasted) + `profile.yaml` (description; YAML
  single-quoted, `'`→`''` escaped — and the reader un-doubles on read).
- `delete_agent(name)` — refuses `default` (manager is protected), `rmtree`s the profile dir, best-effort
  removes the `.local/bin/<name>` alias + `logs/gateways/<name>`.
- API: `POST /api/agents` (create), `DELETE /api/agents/<name>` (delete) — both owner-gated + audited
  `config_change`. UI: "+ Add agent" form; delete is gated behind a **type-the-profile-name** "ARE YOU
  SURE?" confirm (irreversible: soul + memory + learned skills are gone).

## Edge cases / lessons
- MCP `mcp_servers` has **no `cwd` key** → always launch via the wrapper script (or set `env.PYTHONPATH`).
- Multiple per-tenant server processes share one `dtm_ai.db` (sqlite). Low write volume; fine for v1.
  If contention appears, that's the trigger to move the DB to Postgres (D-6).
- Hermes can self-author skills; that NEVER bypasses the fence — its new skills still call our MCP tools,
  which still hit dispatch(). Authoring NEW *DTM AI* tools remains human-merge-gated (D-4) separately.
- The MCP server exposes only *enabled* tools (it honors the kill switch), so disabling a tool in the
  Capability Console immediately removes it from Hermes after `/reload-mcp`.

## Verify
**stdio:** `echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | deploy/hermes/dtm-ai-mcp.sh --tenant acme`
→ JSON list of tools.

**HTTP:** start `python3 -m execution.mcp_server --transport http --port 8089`, then
`curl -s localhost:8089/health` → `{"ok":true,...}`; and (with a token)
`curl -s -H 'Authorization: Bearer $DTM_MCP_TOKEN' -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"system_health","arguments":{}}}' localhost:8089/mcp/acme`
→ envelope with `"tenant_id":"acme"`.

Then in Hermes, ask it to use the tools and confirm entries in
`python3 -m execution.cli audit --tenant acme` with actor=`hermes`.
