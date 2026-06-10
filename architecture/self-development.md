# SOP — Self-Development / Build Studio (A-layer)

> Implements D-8. Code: `execution/core/builder.py`, web `/api/build/*`, dashboard Build tab.
> This is the most safety-sensitive subsystem — read before changing.

## Goal
Let the platform grow new tools the way a developer would (describe → draft → test → ship), but
**without ever letting AI-generated code reach the live system unreviewed.** "It builds on itself" —
safely, with a human merge gate.

## The pipeline (nothing skips a stage)
```
admin describes a tool
  → draft(): LLM writes a candidate module  (NO execution; data never touched)
  → written to skills_candidate/<name>.py    (SANDBOX — not importable as a live tool)
  → validate_candidate(): AST security scan + schema lint
  → admin reviews code + validation in the Build tab
  → promote(): re-validate → copy to execution/skills/ → registry.discover()
  → tool is now LIVE but CATEGORY=read and ENABLED_BY_DEFAULT=False (does nothing until
    enabled in the Capability Console)
```
Reject discards the candidate. The runtime agent can never author tools — only this admin-gated path.

## Owner direct authoring (D-23) — the human fast path
The pipeline above is the AGENT's only road. The **human admin** additionally gets direct CRUD on live
skills from the Capabilities tab (`/api/tools*`, admin-gated, audited `config_change: tool_*`):
- **Edit** `POST /api/tools/<name>/code` — syntax (`ast.parse`) + import + required-attrs validation
  BEFORE the new code goes live; on any failure the previous file is restored byte-for-byte. A `.bak`
  is kept beside the file. The module hot-reloads (no service restart).
- **Add** `POST /api/tools` `{name, code}` — same validation; file lands in `execution/skills/`.
- **Rename** `POST /api/tools/<name>/rename` — rewrites the `NAME = "…"` line + the filename; rejects
  collisions.
- **Delete** `DELETE /api/tools/<name>` — moves the file to `.tmp/deleted_skills/<name>.py` (I-8,
  recoverable by hand), drops it from `sys.modules`, re-discovers.
Safety model: the owner is the trust anchor (the admin Terminal already grants root, D-22) — these
routes never appear as agent tools, so the LLM cannot reach them. Everything stays git-tracked (I-6);
the kill switch (I-4) and the Capability Console still gate execution.

## The validator (`validate_candidate`) — defense in depth
AST-based (not regex), fail-closed. Rejects:
- syntax errors; missing NAME/DESCRIPTION/PARAMETERS/run; non-object PARAMETERS
- CATEGORY other than read/alert (generated tools may NOT be write/destructive)
- imports outside the allowlist (typing/json/re/math/datetime/dataclasses/collections/execution);
  hard-blocks os/sys/subprocess/socket/urllib/requests/importlib/ctypes/pickle/pathlib/threading/…
- forbidden calls: eval/exec/compile/__import__/open/getattr/setattr/…
- dunder attribute access (`__globals__`, `__subclasses__`, …) and `__builtins__`/`__import__` names
- ANY top-level statement other than imports/assignments/the run def/docstring
  (so nothing executes at import time)

The scanner is NOT the only gate — it's one layer. The real gates are: human review of the code,
read-only-by-construction, disabled-on-promote, and dispatch()'s runtime guardrails when the tool
later runs. A determined adversary could obfuscate past a static scan; a human reading the diff is
the backstop, and the promoted tool still can't write or reach un-allowlisted endpoints.

## Why this satisfies "can't break itself"
- Candidates live in a sandbox dir that is NOT on the import path — a bad draft can't run.
- Promotion is a human action; the running tree is only changed by an admin clicking Promote.
- Promoted tools start disabled + read-only → zero blast radius until explicitly enabled.
- Everything is a file + git: `git revert`/delete is the rollback. Audit logs every draft/promote/reject.
- The runtime serving clients has no authoring ability; this path is separate and admin-only.

## Edge cases / lessons
- A name collision with an existing live tool is refused at promote.
- `skills_candidate/*.py` is gitignored (work-in-progress, possibly unreviewed).
- In dev with no real LLM key, draft() returns the mock echo, which fails validation — expected;
  point the Build tab's model selector at Claude (key added in Integrations) for real drafts.
- Future hardening: run the actual test suite against the candidate in an isolated worktree before
  allowing promote (right now validation is static + human review).
