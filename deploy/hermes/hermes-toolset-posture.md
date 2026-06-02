# Hermes Native Toolset Posture (MSP profile)

Hermes ships ~70 tools across ~28 toolsets. Below is the recommended starting posture for the
`msp` profile that drives client environments. **Rule of thumb: Hermes does MSP work through the
DTM AI MCP tools (which are guarded). Hermes' own native tools that act locally/on the host start
OFF and open only as you validate.**

This is *guidance for `hermes tools` / `~/.hermes/config.yaml`* — Hermes controls its own native
toolsets. DTM AI's Capability Console controls the MSP tools (the ones that reach client systems).

| Hermes toolset | What it does | MSP start | Notes |
|---|---|---|---|
| **DTM AI MCP tools** (`mcp_dtm_*`) | Guarded MSP reads + memory | **ON** | The safe path; every call hits DTM AI dispatch(). |
| `memory` / `session_search` | Hermes' own cross-session memory | ON | Complements DTM AI's per-client `memory.md`. |
| `web` (search/extract) | Web search | Optional | Fine for research; no client-system access. |
| `terminal` | Shell on the HOST | **OFF** | Runs on the Hermes host, not fenced. Open only for a trusted admin profile, never for client work. |
| `execute_code` | Arbitrary code | **OFF** | Same risk as terminal. Keep off for MSP. |
| `file` (write/patch) | Edit files on the host | **OFF** | Host filesystem writes; not a guarded MSP action. |
| `browser` (click/type) | Drive a browser | **OFF** initially | Powerful + un-fenced; enable later only for specific, supervised automations. |
| `delegation` / subagents | Spawn subagents | Optional | They inherit the profile's toolset — keep that minimal. |
| `cronjob` | Hermes self-schedules tasks | **OFF** initially | Autonomy ramp item; enable once behavior is trusted. |
| `media`, `home_assistant`, chat-platform admin | misc | OFF | Not needed for MSP; reduces tool bloat + attack surface. |

## Ramp-to-autonomy (the safe order)
1. **Reads only** — DTM AI MCP read tools + Hermes `memory`/`web`. Prove answers are correct + sourced.
2. **Internal writes** — DTM AI `memory_note` (already safe). Let Hermes build per-client memory.
3. **Guarded MSP writes** — when DTM AI's approval workflow ships, open a specific DTM AI write tool
   in the Capability Console (`allow_write`, keep `require_approval` on at first).
4. **Host capabilities** — only if you truly need them, and only in a separate admin profile, never
   the one bound to client tenants.

Every step is reversible: disable in the Capability Console (MSP tools) or `hermes tools` (native).
