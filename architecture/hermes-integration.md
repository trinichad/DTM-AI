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
- **One MCP server process per client.** A `dtm_<client>` entry in `~/.hermes/config.yaml` launches
  `deploy/hermes/dtm-ai-mcp.sh --tenant <client>`. Hermes namespaces its tools `mcp_dtm_<client>_*`,
  so the brain physically cannot cross tenants through the tools.
- The launcher sets cwd; the server resolves `.env`, `dtm_ai.db`, and `vault/` from its own location
  (`__file__`), so it is cwd-independent (verified launching from `/tmp` via `PYTHONPATH`).

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

## Edge cases / lessons
- MCP `mcp_servers` has **no `cwd` key** → always launch via the wrapper script (or set `env.PYTHONPATH`).
- Multiple per-tenant server processes share one `dtm_ai.db` (sqlite). Low write volume; fine for v1.
  If contention appears, that's the trigger to move the DB to Postgres (D-6).
- Hermes can self-author skills; that NEVER bypasses the fence — its new skills still call our MCP tools,
  which still hit dispatch(). Authoring NEW *DTM AI* tools remains human-merge-gated (D-4) separately.
- The MCP server exposes only *enabled* tools (it honors the kill switch), so disabling a tool in the
  Capability Console immediately removes it from Hermes after `/reload-mcp`.

## Verify
`echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | deploy/hermes/dtm-ai-mcp.sh --tenant acme`
→ JSON list of tools. Then in Hermes, ask it to use the tools and confirm entries in
`python3 -m execution.cli audit --tenant acme` with actor=`hermes`.
