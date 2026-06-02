"""DTM AI skills (tools) — the T-layer capabilities.

Every module here exporting NAME / DESCRIPTION / PARAMETERS / run is auto-discovered
by core.registry (Invariant I-1). Conventions:
  - snake_case NAME, integration-prefixed (e.g. kaseya_*, cylance_*, huntress_*)
  - CATEGORY defaults to "read"; only read/alert tools ship in v1
  - run(ctx, **kwargs): ctx is the ToolContext (tenant-scoped); return JSON-serializable
    data, or {"error": "..."} on failure (never raise for expected errors)

Files prefixed with "_" are skipped by discovery (helpers / not-yet-live).
"""
