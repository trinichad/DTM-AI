# SOP — Memory & Knowledge Vault (A-layer)

> A.N.T. golden rule: if this logic changes, update this SOP **before** the code.
> Implements D-13, D-20. Code: `execution/core/memory.py`, skills `kb_search` / `memory_read` /
> `memory_note` / `memory_update`.

## Goal
Give DTM AI a knowledge base + per-client long-term memory so it behaves like an employee who
remembers context — using plain markdown so it's human-editable (open as an Obsidian vault),
git-trackable, backup-able, and auditable. Per-client memory is a **living record of the client's
current environment** (D-20): read AND updated as things change — corrected, edited, pruned — not an
append-only log.

## Layout
```
vault/                          # path = DTM_VAULT_PATH (default <project>/vault); GITIGNORED (client data)
├── kb/                         # knowledge base: runbooks, SOPs, network docs (humans curate here)
│   └── <area>/<topic>.md
└── clients/
    └── <tenant>/memory.md      # living record of one client's current environment (+ .bak rollback)
```

## Tools
| Tool | Category | Notes |
|---|---|---|
| `kb_search(query, limit?)` | read | all query terms must appear in a doc; ranked by term frequency; returns `{doc, score, snippet}`. |
| `memory_read()` | read | returns the bound tenant's `memory.md` text (empty if none). |
| `memory_note(note)` | write (internal) | ADD a new fact — appends `- <iso> (<actor>): <note>` to the tenant's `memory.md`; refuses tenant `*`. |
| `memory_update(content)` | write (internal) | CHANGE/REMOVE facts — overwrites the tenant's whole `memory.md` with a revised version (prior kept as `memory.md.bak`); refuses tenant `*`. Read first, revise, write the full doc. |

The dashboard Memory tab edits the same `memory.md` directly (an editable textarea → `POST /api/memory
{tenant, content}` → `write_memory`); `{tenant, note}` still appends. KB docs have full CRUD
(`GET/POST/DELETE /api/kb`, `POST /api/kb/rename`) and clients are a registry (`GET/POST/DELETE
/api/clients`) — all owner-gated + audited; bundled `reference/` docs stay read-only.

## Security / invariants
- **Internal write ≠ client-system write.** `memory_note`/`memory_update` write ONLY to our vault, so they
  are allowed by default (seeded `allow_write=True, require_approval=False` in `runtime.build_agent`) — but
  each is a first-class entry in the Capability Console and can be disabled there. The "read-only by default"
  floor governs CLIENT systems, not DTM AI's own memory.
- **Overwrite keeps a rollback.** `write_memory` copies the prior `memory.md` to `memory.md.bak` before
  writing, so a bad edit (human or agent) is one copy away from recovery. `.bak` is never auto-injected or
  listed in the UI (read path is `memory.md` only).
- **Path safety:** tenant ids are sanitized (`_safe_tenant`) to a single safe path segment — no traversal.
- **Tenant isolation:** memory is per `<tenant>`; all memory tools use the bound tenant only.
- Every call is still audited via dispatch().

## Two KB sources (both searched by `kb_search`)
- **`vault/kb/`** — the owner's own runbooks/SOPs (under `DTM_VAULT_PATH`, gitignored, per-deployment,
  editable as an Obsidian vault).
- **`reference/`** (repo root, **git-tracked**) — bundled vendor references that ship WITH the app and
  deploy via `git pull`, no per-server copying. First entry:
  `reference/kaseya-vsa9-agent-procedure-commands.md` (the 77 Kaseya VSA9 STEP/agent-procedure commands
  from help.vsa9.kaseya.com — REFERENCE ONLY, not executable; DTM AI v1 is read-only).
  `VaultStore._kb_files()` scans both; doc ids are relative to their base (`kb/…` vs `reference/…`). To add
  a vendor reference, drop a `.md` in `reference/` and commit — instantly searchable. Use `reference/` for
  public/vendor docs (shared, version-controlled); use `vault/kb/` for client-specific or owner-private notes.

## Edge cases / lessons
- `memory_note`/`memory_update` on tenant `*` return `{"error": ...}` (cross-client memory is meaningless) → error envelope.
- `memory_update` overwrites the whole doc — the agent is instructed to `memory_read` first and resend the
  FULL revised text; the `.bak` is the safety net if it drops something.
- `kb/` missing or empty → `kb_search` returns `[]` (never raises); the bundled `reference/` is still searched.
- Future: swap the simple term-match for embeddings/FTS5, and add a guarded connector to a real doc
  system (IT Glue/Hudu/SharePoint) feeding the same `kb_search` contract.
