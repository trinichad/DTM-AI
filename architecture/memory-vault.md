# SOP — Memory & Knowledge Vault (A-layer)

> A.N.T. golden rule: if this logic changes, update this SOP **before** the code.
> Implements D-13. Code: `execution/core/memory.py`, skills `kb_search` / `memory_read` / `memory_note`.

## Goal
Give DTM AI a knowledge base + per-client long-term memory so it behaves like an employee who
remembers context — using plain markdown so it's human-editable (open as an Obsidian vault),
git-trackable, backup-able, and auditable.

## Layout
```
vault/                          # path = DTM_VAULT_PATH (default <project>/vault); GITIGNORED (client data)
├── kb/                         # knowledge base: runbooks, SOPs, network docs (humans curate here)
│   └── <area>/<topic>.md
└── clients/
    └── <tenant>/memory.md      # the agent's long-term notes for one client (employee notebook)
```

## Tools
| Tool | Category | Notes |
|---|---|---|
| `kb_search(query, limit?)` | read | all query terms must appear in a doc; ranked by term frequency; returns `{doc, score, snippet}`. |
| `memory_read()` | read | returns the bound tenant's `memory.md` text (empty if none). |
| `memory_note(note)` | write (internal) | appends `- <iso> (<actor>): <note>` to the tenant's `memory.md`; refuses tenant `*`. |

## Security / invariants
- **Internal write ≠ client-system write.** `memory_note` writes ONLY to our vault, so it is allowed by
  default (seeded `allow_write=True, require_approval=False` in `runtime.build_agent`) — but it is a
  first-class entry in the Capability Console and can be disabled there. The "read-only by default" floor
  governs CLIENT systems, not DTM AI's own notebook.
- **Path safety:** tenant ids are sanitized (`_safe_tenant`) to a single safe path segment — no traversal.
- **Tenant isolation:** memory is per `<tenant>`; `memory_read`/`memory_note` use the bound tenant only.
- Every call is still audited via dispatch().

## Edge cases / lessons
- `memory_note` on tenant `*` returns `{"error": ...}` (cross-client memory is meaningless) → error envelope.
- `kb/` missing or empty → `kb_search` returns `[]` (never raises).
- Future: swap the simple term-match for embeddings/FTS5, and add a guarded connector to a real doc
  system (IT Glue/Hudu/SharePoint) feeding the same `kb_search` contract.
