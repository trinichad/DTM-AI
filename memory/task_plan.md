# task_plan.md — DTM AI Build Plan

**North Star:** A secure, read-only conversational assistant the DTM team chats with to check things
across all client environments (devices, users, security posture, alerts) and get instant, sourced
answers — no write actions in v1.
**Phase-L green credentials (verifiable today):** Kaseya VSA, Cylance, Huntress. All others = read-only
stubs, wired in Phase 3 as creds become available (M365/Entra next).
**Status:** Blueprint phase. HALT on `/execution/` logic until this plan is signed off.

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

## Phase 2 — Agent Toolkit
- [ ] Tool permission/risk model enforced in `dispatch()` (read/alert/write/destructive)
- [ ] Approval workflow: write → proposed-action record → human approve → one-shot token consumed
- [ ] Tool/library management UI (enable/disable, risk, last-run, AI rules)
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
