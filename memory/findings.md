# findings.md — Research & Discovery

> Source: parallel deep-read of `Kaseya Link` and `ClaudeOS [Hermes] V2` (recon workflow, 2026-06-01).

## Existing assets to REUSE

### From `Kaseya Link` (Python backend — the real backbone)
- **Auto-discovery tool registry** — `Kaseya Link /tools/__init__.py`. Drop a `.py` exporting
  `NAME / DESCRIPTION / PARAMETERS / run` and it's discovered via `pkgutil.iter_modules` +
  `importlib`; `to_schema()` emits the OpenAI/Ollama function-call shape. No central list to edit.
  → **single best asset in either repo.** Becomes DTM AI's `skills/` registry.
- **Fail-closed credential layer** — `Kaseya Link /web/credentials.py`. `CredentialSpec` registry +
  `require()` refuses to build a client if any key is missing (no None-bearing API calls);
  fingerprint-only surfacing (`sha256(value)[:7]`); refuses to boot if `.env` is group/world-readable.
- **Per-integration health probes** — smallest auth-proving call returning `{ok, detail, latency_ms}`,
  never raises to UI. → per-client health tiles.
- **Alert reconciliation state machine** — `Kaseya Link /web/db.py`. upsert-open / clear-not-seen by
  dedup key; open→acknowledged→cleared. → MSP alert/ticket flow.
- **Read-only vendor clients** — `kaseya_client.py` (VSA 9.5, Bearer or cached /auth token),
  `cylance_client.py` (JWT exchange — REPLACE hand-rolled JWT w/ PyJWT), `huntress_client.py`
  (Basic auth + thread-safe sliding-window rate limiter + 429 backoff), `freshdesk_client.py`
  (the only writer, isolated).
- **Bounded defensive agent loop** — `ollama_kaseya_agent.py`: 8-round cap, 20K result truncation,
  dict-or-string arg parse, tools return `{"error": ...}` instead of raising.
- **Hardened systemd deploy** — `deploy/kaseya-ai.service`: dedicated service user, `NoNewPrivileges`,
  `ProtectSystem=strict`, `ProtectHome=read-only`, scoped `ReadWritePaths`, `PrivateTmp`,
  binds `127.0.0.1:8088` behind nginx. `SETUP_GUIDE.md` is a full from-zero Ubuntu install manuscript.

### From `ClaudeOS [Hermes] V2` (design donor ONLY — per user)
- **App shell** — `src/routes/__root.tsx` + `src/components/app-sidebar.tsx`: fixed sidebar + sticky
  blurred header + Outlet; data-driven nav w/ active-route detection.
- **Status-surface kit** — `StatusPill`, circular `Dial` gauge, `WindowBar` (caps + reset countdowns),
  KPI sparkline panels (`src/components/usage-panel.tsx`).
- **Vendor tile registry** — `src/components/model-logos.tsx`: typed registry + brand-color tiles.
- **CSRF-gated refresh** — `usage-panel.tsx`: `/__token` → `X-Claude-OS-Token` header → POST.
  Pattern for every privileged/mutating endpoint.
- **Single-snapshot data discipline** — `src/lib/use-live-data.ts`: one normalized JSON read through a
  query hook; swap source without touching components. + demo/cold/real tri-state.
- **LLM-prescription-as-cron** — `skills/dream/SKILL.md`: scored findings (severity × $ × certainty),
  strict JSON output, stable-slug IDs + `state.json` age-tracking, "write nothing if no signal".
  → MSP recurring health-check/audit contract.
- **launchd/cron installer** — `scripts/install-dream-cron.ts`: absolute bin paths, idempotent
  unload→load, crontab fallback. Template for scheduled MSP tasks.
- Foundation: TanStack Start/Router/Query, Tailwind v4, shadcn/Radix/CVA component set.

## Existing weaknesses to REPLACE (these ARE the unmet security requirements)
- **Guardrails are prose-only.** CATEGORY (read/alert/write) + "ask before write / decline destructive"
  live in the system prompt + CLAUDE.md but the loop NEVER enforces them. → must be code in `dispatch()`.
- **No model routing** — Ollama host + single model hardcoded; no provider abstraction, no fallback.
- **No schema validation** of LLM tool args before `run(**args)`.
- **Unwired write gate** — `KASEYA_AI_FRESHDESK_AUTOCREATE` defined, never consumed.
- **Single shared autocommit SQLite connection** across threads → concurrency hazard. → Postgres.
- **No session TTL/rotation**; `verify_user` returns `False` on bcrypt exception (should fail closed).
- **Hermes:** 4,000-line `index.tsx` monolith w/ module-global `let` re-derived per render (documented
  bug source); `liveData` typed `any` rendered straight into style/URL strings (injection surface);
  fabricated metrics (token counts, ROI heuristics, unverified-JWT plan detection, `dump-keychain`);
  single-home path coupling. → none of these carry into DTM AI.
- **Dream auto-run `command` string** executed by a button → never free-form shell on an MSP platform.

## Exact tool-definition schema (carried forward, hardened)
Each tool module exports:
- `NAME: str` — snake_case, unique
- `DESCRIPTION: str` — one line, shown to the LLM
- `PARAMETERS: dict` — JSON-Schema object (will now be VALIDATED, not trusted)
- `CATEGORY: str` — `"read" | "alert" | "write" | "destructive"` (will now be ENFORCED in dispatch)
- `ENABLED_BY_DEFAULT: bool`
- `RISK_LEVEL`, `REQUIRES_APPROVAL` — NEW fields to add
- `def run(ctx, **kwargs)` — `ctx` carries tenant + scoped clients (replaces bare `kaseya` arg)
