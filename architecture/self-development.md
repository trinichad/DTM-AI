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
- **Move / group management** — the Capabilities groups ARE the tools' `SOURCE` labels (also used in
  result citations). `POST /api/tools/<name>/source` rewrites one tool's SOURCE line (inserting it
  after NAME when the module relied on the prefix default); a NEW group name simply creates that
  group (groups are derived — empty ones can't exist). `POST /api/tools/groups/rename` rewrites
  SOURCE on every tool in a group. The security CATEGORY enum (read/alert/write/destructive) is NOT
  group metadata — it's enforced in dispatch() and changes only via a deliberate code edit.
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

---

## Amendment (2026-06-11, D-40) — agent-initiated proposals + write-category candidates

**The agent can now OFFER to grow a capability.** When a chat request needs a tool that doesn't
exist (e.g. "create an M365 user with MFA"), the agent no longer just declines: it says exactly
what's missing and asks the owner whether to draft it. On an explicit yes it calls the new
`propose_tool` skill (SOURCE=msp_ai), which runs the SAME `builder.draft()` pipeline the Build tab
uses — the LLM-written candidate lands in `skills_candidate/` (sandbox, never importable live) and
shows up in the Build tab for the owner to review → test → Promote → enable. The agent still
cannot write tool code itself, cannot promote, and cannot enable: `propose_tool` only feeds the
existing human gate. Flow: **agent asks → owner "yes" → draft staged → owner tests/approves →
owner enables → only then can the agent run it.**

**Write-category candidates are now allowed — with hard floors.** v1's "generated tools may not be
write" gate is opened deliberately (the owner asked for buildable write actions); destructive
stays banned. The validator enforces for every candidate:
- `CATEGORY` ∈ read / alert / **write** (destructive rejected);
- a write candidate MUST declare `REQUIRES_APPROVAL = True` (rejected otherwise) — so every run
  of the promoted tool lands in the Approvals tab first (the gate can later be relaxed per-tool
  in the Capability Console, Rule #1);
- `ENABLED_BY_DEFAULT` must be False/absent for ALL candidates (rejected if True);
- a candidate whose code references `scoped_write` must be CATEGORY=write (a "read" tool can't
  smuggle writes past the console's allow_write gate).
Defense-in-depth on a promoted write tool: starts disabled (console) + allow_write defaults OFF
+ per-run approval + `scoped_write` path allowlist + audit. Five gates before a write reaches a
client system.

**Write primitives.** `clients/scopes.py` gains `WRITE_SCOPES` (per-vendor allowlist of writable
path prefixes; v1: m365 `/users` only) and `scoped_write(ctx, vendor, path, body, method)` —
POST/PATCH only, same no-host-escape rules as reads; DELETE is deliberately unsupported.
`M365Client` gains `post()`/`patch()`. NOTE: Graph writes need write scopes consented — the owner
sets `M365_SCOPES` (e.g. + `User.ReadWrite.All UserAuthenticationMethod.ReadWrite.All`) and that
client signs in again (see m365-graph.md).

### Lesson (2026-06-11) — first real agent-proposed write draft
The owner had the agent draft "create shared mailbox + Full Access/Send As". Three findings:
1. **Validator hole found & fixed:** the draft contained `import traceback` *inside* `run()` —
   the old validator checked import roots only at the TOP level, so nested imports (`import os`
   in a function body) bypassed the allowlist entirely. Import roots are now checked across the
   whole AST (`ast.walk`), same as the call/dunder checks.
2. **Models hallucinate endpoints.** The draft invented Graph paths
   (`/admin/Exchange/sharedMailbox/create`, `/permissions/fullaccess/assign`) that do not exist.
   Defense-in-depth held: even if validation had passed and the owner had promoted+enabled it,
   `scoped_write` would have refused every one of those paths (only `/users` is allow-listed).
   Human review of WHAT THE TOOL CLAIMS TO CALL is part of the Build-tab test step — check the
   endpoints against the vendor's real API before promoting.
3. **Know the platform boundary.** Shared-mailbox creation and mailbox permissions (Full Access /
   Send As) are NOT exposed by Microsoft Graph at all — they are Exchange Online capabilities
   (EXO PowerShell / admin center). No re-draft can fix that; such a request needs an Exchange
   Online connector (a separate, deliberate integration) or stays a human runbook step.

### Lesson (2026-06-11) — AI-drafted Exchange "delete mailbox" tool (why it failed)
Owner had the agent draft `exo_remove_mailbox`, promoted+enabled it, and approved a deletion. Two
failures, both instructive:
1. **Wrong subsystem.** The draft used `scoped_read/scoped_write(ctx, "m365", "/mailbox/...")`.
   Exchange is NOT a Graph scoped-path vendor — it is the `exo` cmdlet connector
   (`ctx.client("exo").invoke`). The write `/mailbox/remove` was correctly BLOCKED by the m365
   write allowlist (only `/users`) — so the mailbox was never deleted. Defense-in-depth held.
   (There is also deliberately NO Remove/delete cmdlet in the EXO allowlist; deletion is not a
   capability of this connector.)
2. **False success.** scoped_write returned `{"error": "write blocked: ..."}` as a VALUE; the tool
   ignored it and returned `status: "executed"` with the error buried in `vendor_response`.
   dispatch only treats a TOP-LEVEL `"error"` as failure, so it reported ok=True and the chat
   said "✅ executed" for an action that never ran.
Fixes: the draft PROMPT now (a) documents the EXO cmdlet connector + that there is no delete, and
(b) adds a CRITICAL rule — inspect every connector result and surface a top-level `{"error": ...}`,
never report success when the underlying call was blocked/errored. Operational rule reaffirmed:
**test a promoted tool against the real API before enabling it** — the human review/test step is
the backstop the static validator can't replace. Real Exchange deletion would require adding
`Remove-Mailbox` to the EXO allowlist as a deliberate DESTRUCTIVE capability (owner decision),
which `propose_tool` cannot create (it stages read/alert/write only, never destructive).

---

## Amendment (2026-06-12, D-64) — owner-approved connector self-extension

Problem (owner hit it live): when the AI is asked to build a tool that needs an Exchange cmdlet
NOT on the connector allowlist, the draft can only REFUSE (the validator rightly forbids generated
code from improvising around the allowlist, and the AI cannot add to it). The owner — the trust
anchor (I-5) — wants "I asked, so build it" to actually work, without a developer in the loop.

Design — a SECOND owner gate, not a removed one:

- New write tool **`propose_connector_capability`** (CATEGORY=write, REQUIRES_APPROVAL=True,
  ENABLED_BY_DEFAULT=False). The agent calls it when it needs a cmdlet outside the allowlist:
  it proposes `{connector, cmdlet, kind(read|write), params[], reason}`. dispatch pauses it as a
  normal approval — the owner sees the EXACT cmdlet + params + reason on the approval card. On
  approval, `run()` persists the grant; the D-62 continuation then lets the agent finish building.
- Grants live in **`core/connector_grants.py`** (JSON under `<vault>/connector_grants.json`, 0600,
  git-trackable per I-6). `EXOClient` merges built-in `ALLOWED_CMDLETS`/`PARAM_ALLOWLIST` with the
  granted ones at call time (`build_exo` loads them); a granted cmdlet is param-allowlisted to
  EXACTLY the approved params (always enforced, unlike some built-ins).

HARD FLOORS the self-extension can NEVER cross (enforced in `connector_grants.can_grant`, checked
again in the tool):
- **No destructive grants.** `kind` is read|write only; any cmdlet in `exo.DESTRUCTIVE_CMDLETS` or
  in the curated `FORBIDDEN` denylist (Remove-Mailbox, Remove-MailboxDatabase, mailbox EXPORT /
  compliance-search exfil cmdlets, audit-config tamper cmdlets) is refused. Data deletion stays
  hand-written + the D-54 destructive floor — self-extension can never reach it.
- **New cmdlets only.** A cmdlet already built-in is refused ("already available") — grants can't
  WIDEN the params of an existing curated cmdlet (e.g. can't bolt litigation-hold onto Set-Mailbox).
- Every granted cmdlet still runs through `invoke()`, so a tool using it is STILL gated by enable +
  allow_write + per-run approval. The grant widens WHAT cmdlets exist; it never widens the
  read-only/approval posture.
- Honest limit: the FORBIDDEN denylist is a backstop, not exhaustive — the real control is that the
  owner reviews and approves every single grant, sees it in the Capabilities/approvals UI, and can
  revoke it (`POST /api/connector-grants/revoke`) any time. Restart does NOT clear grants (they're
  persisted, unlike batch grants) — revoke is the off switch.

Graph paths (scoped_read/scoped_write) are a future extension of the same store; this lands EXO
cmdlets, which was the concrete gap. (New Graph *scopes* are a Microsoft consent action — re-sign-in
— not something any self-extension can grant.)
