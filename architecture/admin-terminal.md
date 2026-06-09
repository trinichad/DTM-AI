# SOP — Admin Terminal (A-layer)

> A.N.T. golden rule: if this logic changes, update this SOP **before** the code.
> Implements **D-21**. Code: `execution/core/adminshell.py`, routes `GET/POST /api/terminal`
> (`execution/web/api.py`), UI tab `VIEWS.terminal` (`dashboard/index.html`).

## Goal
Let a logged-in **admin** run shell commands on the host from a dashboard tab — a convenience over
opening SSH. This is the constitution's one explicit, **human-only** exception to Rule #6 ("no free-form
shell"). It is NOT available to the AI/agent loop: `dispatch()` and the registry are untouched; the agent
still has zero shell access.

## Flow
```
admin types command → POST /api/terminal {command}
  → _require_admin(role)            (non-admins: 403; nav item hidden for them too)
  → terminal_enabled()?             (DTM_ADMIN_TERMINAL=0 → 403)
  → audit.record(action="terminal", actor, detail=command)   ← logged BEFORE it runs
  → AdminShell.run(user, command)   → bash -c <command> in the user's tracked cwd
  → {ok, stdout, stderr, exit_code, cwd}
```
`GET /api/terminal` returns `{enabled, cwd, user, host}` to seed the tab.

## Guardrails (enforced in code)
| Control | How |
|---|---|
| Admin-only | route gated by `_require_admin(role)`; nav item filtered to `ME.role==='admin'` (with `build`). |
| Audited | `audit.record(..., action="terminal", detail=command[:500])` runs **before** execution. |
| Unprivileged | runs as the `dtm-ai` service user — **no sudo wired**. Root actions still require SSH-as-ross. |
| Sandboxed | systemd `ProtectSystem=strict` + `ReadWritePaths=/opt/dtm-ai`: writes confined to the app dir. |
| Bounded | per-command timeout (30s) + output cap (100k chars/stream); one fresh process per command. |
| Kill switch | `DTM_ADMIN_TERMINAL=0` (or false/off/no) → `terminal_enabled()` false → tab + endpoint disabled (I-4). |

## Behaviour / limits
- **Not an interactive PTY** — no `vim`, `top`, pagers, or programs that read stdin. Each command is a
  one-shot `bash -c`. Output (stdout+stderr) is returned after it exits or times out.
- **`cd` persists per user** (tracked in-memory, thread-safe) so it feels like a session — but only when
  `cd` is the whole command (no `&&`/`;`/`|`). Everything else runs in the tracked working directory.
  Env exports do NOT persist (fresh process each time).
- Output is rendered with `textContent` in the UI, so command output can never inject HTML/JS.

## Accepted residual risk (D-21)
A stolen admin session, CSRF, or an XSS hole = command execution as `dtm-ai`, which can read the app +
vault (incl. client memory) and write within `/opt/dtm-ai`. This is the cost of the convenience; it is
why the feature is admin-only, audited, unprivileged, and kill-switchable. For the lowest-risk posture,
set `DTM_ADMIN_TERMINAL=0` and use SSH.

## Edge cases / lessons
- Tracked cwd vanished (deleted out from under us) → resets to the project base, never errors.
- Command that exits non-zero with no stderr → UI shows `(exit N)`.
- Timeout → `{exit_code: 124, stderr: "(timed out after 30s)"}`.
- `ThreadingHTTPServer` means a long command runs in its own thread and does not block the dashboard.
