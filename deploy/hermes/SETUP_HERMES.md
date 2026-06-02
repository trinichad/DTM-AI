# Hermes Agent ⇄ DTM AI — Setup Guide (Ubuntu)

Wire **Nous Research Hermes Agent** to DTM AI as a *fenced brain*: Hermes gets memory,
self-improving skills, and multi-channel reach, but it can only touch client systems
through DTM AI's guarded tools (read-only by default, capability/approval/tenant/audit all
enforced by DTM AI's `dispatch()` — see `../../architecture/hermes-integration.md`).

> Run these on the Ubuntu server where the local LLM lives. Assumes DTM AI is at
> `/opt/dtm-ai` (set `DTM_AI_HOME` otherwise). Sources at the bottom.

---

## 0. Prerequisites
- DTM AI deployed at `/opt/dtm-ai` and working: `cd /opt/dtm-ai && python3 -m execution.cli probe`
  shows your integrations green (Kaseya/Cylance/Huntress creds in `.env`).
- Ollama running with a tool-capable model, e.g. `ollama pull qwen2.5:14b` (≥64K context).

## 1. Install Hermes
```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```
The installer handles Python/Node/ripgrep/ffmpeg, the venv, and the global `hermes` command.

## 2. Point Hermes at your LOCAL model (privacy-first)
```bash
hermes model
#  → choose "Custom endpoint (self-hosted / VLLM / etc.)"
#  → URL: http://127.0.0.1:11434/v1     (Ollama's OpenAI-compatible endpoint)
#  → API key: (leave blank)
```
This keeps reasoning on the local LLM. (You can add a cloud provider later for heavy tasks —
DTM AI's own router already enforces local-first for client data independently.)

## 3. Make the DTM AI launcher executable
```bash
chmod +x /opt/dtm-ai/deploy/hermes/dtm-ai-mcp.sh
# sanity check (Ctrl-D after it prints nothing / waits):
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | /opt/dtm-ai/deploy/hermes/dtm-ai-mcp.sh --tenant acme
#  → should print a JSON line listing the DTM AI tools
```

## 4. Register DTM AI as an MCP server
Edit `~/.hermes/config.yaml` and paste the `mcp_servers` block from
[`config.snippet.yaml`](config.snippet.yaml) (one `dtm_<client>` entry per client). Then,
inside a Hermes session:
```
/reload-mcp
```
Hermes connects and exposes the tools as `mcp_dtm_acme_kaseya_list_assets`, etc.
(Or use the discovery-first CLI: `hermes mcp` to connect + pick tools interactively.)

## 5. FENCE Hermes' own dangerous tools (important)
Hermes ships ~70 native tools including `terminal`, `execute_code`, `write_file`, and browser
control. For an MSP brain that touches client data, **start these OFF** and open them slowly.
See [`hermes-toolset-posture.md`](hermes-toolset-posture.md) for the recommended matrix. Use a
dedicated minimal profile for MSP work:
```bash
hermes -p msp setup        # a separate profile (own HERMES_HOME/config/memory)
hermes -p msp tools        # disable terminal/code/file-write/browser toolsets
```
Keep the DTM AI MCP tools enabled; that's how Hermes does MSP work — safely.

## 6. Verify end-to-end
In a Hermes session (msp profile):
```
> Using the DTM AI tools, how many assets does the acme client have, and any open Huntress incidents?
```
Confirm Hermes calls `mcp_dtm_acme_kaseya_list_assets` / `mcp_dtm_acme_huntress_list_incidents`,
then check the DTM AI audit log: `cd /opt/dtm-ai && python3 -m execution.cli audit --tenant acme`.
You should see the tool calls recorded with actor=`hermes`.

## 7. (Optional) Always-on + multi-channel
`hermes gateway setup` to reach the agent from Slack/Teams/email, etc. Keep the `msp` profile's
toolset minimal; widen capabilities only as trust grows — in BOTH places: DTM AI's Capability
Console (the MSP tools) and `hermes tools` (Hermes' native tools).

---
## The two control planes (where you turn things on)
- **DTM AI Capability Console** (dashboard) → governs the *MSP tools* that touch client systems
  (read-only by default; `allow_write`/`require_approval` per tool). This is the safety boundary.
- **Hermes `tools` / MCP `tools.include`** → governs *Hermes' own* native toolsets and which MCP
  tools it sees. Keep dangerous native tools off for MSP work.

Open both gradually as you validate — that's the path to "works on its own", safely.

---
### Sources
- Install / quickstart: https://hermes-agent.nousresearch.com/docs/getting-started/installation
- MCP config reference: https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference
- Use MCP with Hermes: https://hermes-agent.nousresearch.com/docs/guides/use-mcp-with-hermes
- Ollama provider: https://hermes-agent.nousresearch.com/docs/integrations/providers · https://docs.ollama.com/integrations/hermes
