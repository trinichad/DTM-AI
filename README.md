# DTM AI

A private, secure AI operations platform for **DTM Consulting** (IT MSP). Chat with an AI agent to
inspect client environments across Kaseya, Cylance, and Huntress — read-only by default, every action
audited, capabilities you open tool-by-tool. Built to grow itself safely (learned skills + a gated
self-development studio) without breaking what's running.

> The full design + decisions live in [`CLAUDE.md`](CLAUDE.md) (the Project Constitution),
> [`architecture/`](architecture/) (SOPs), and [`memory/`](memory/) (plan, decisions, progress).

## Run it
```bash
python3 -m execution.web          # dashboard + API at http://127.0.0.1:8088
python3 -m execution.cli health   # CLI: health / tools / probe / chat / caps / audit
```
Stdlib-only core + web (no build step, no virtualenv). Deploy: [`deploy/SETUP.md`](deploy/SETUP.md).
Tests: `python3 -m unittest discover -s tests` (122 passing).

## Architecture (A.N.T.)
```
Dashboard (self-contained Tailwind SPA)  ──REST/JSON──▶  FastAPI-style stdlib web layer
                                                              │
   Navigation: model router (local Ollama default; Claude/OpenAI opt-in) + bounded agent loop
   Tools:      auto-discovery skills registry ─▶ dispatch()  ◀── the guardrail chokepoint
                                                              │
   read-only by default · tenant isolation · arg validation · audit · approval workflow
                                                              │
   scoped vendor clients (Kaseya/Cylance/Huntress)  ·  Obsidian-style memory/KB vault
```
A separate **MCP server** exposes the guarded tools so **Hermes Agent** (Nous Research) can be the
brain — fenced: however autonomous it is, it reaches clients only through `dispatch()`.

## Dashboard
Overview · Chat (pick any model) · Integrations (vendor + AI keys, secure entry) · Capabilities
(the enable/allow-write/approval throttle) · Skills (Hermes' learned skills) · Memory (per-client
notes + KB) · Approvals (sign off on write actions) · Build (draft new tools, sandboxed) · Audit · Settings (users).

## Security model (enforced in code, not prose)
- **Read-only by default**; writes gated by the Capability Console + a human **approval workflow**
  (propose → approve → execute, args-bound, one-shot). Destructive always needs per-action approval.
- **Tenant isolation**, **JSON-Schema arg validation**, **append-only audit** on every call.
- **Secrets**: 0600 + fingerprint-only display; entered in-app, never echoed back, never in git.
- **Self-development is gated**: AI drafts tools into a sandbox, an AST scanner + human review +
  read-only-on-promote keep generated code from ever breaking the live system. git is the rollback.

## Layout
```
CLAUDE.md            Project Constitution            execution/core/    secure core (dispatch, registry, …)
architecture/        A-layer SOPs                    execution/skills/  the tools (read-only)
memory/              plan · findings · decisions     execution/clients/ scoped vendor clients
dashboard/index.html the SPA                         execution/web/     API + auth + server
deploy/              systemd · nginx · SETUP · Hermes tests/             122 tests
```
