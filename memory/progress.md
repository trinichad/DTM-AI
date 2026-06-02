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

### Next (Phase 1 — Push 2 & 3)
- Push 2: port Kaseya/Cylance/Huntress clients (PyJWT for Cylance) → `clients/` + wire
  `client_factory` into ToolContext + real read-only skills + per-integration probes.
- Push 3: FastAPI app (REST + WebSocket chat, sessions w/ TTL) + dashboard shell (Hermes donor).

### Errors / tests
- All green. ResourceWarning (unclosed sqlite) fixed by adding AuditStore.close().
