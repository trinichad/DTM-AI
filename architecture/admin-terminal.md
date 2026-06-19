# SOP — Admin Terminal (A-layer)

> A.N.T. golden rule: if this logic changes, update this SOP **before** the code.
> Implements **D-21 + D-22**. Code: `execution/core/adminshell.py`, routes `GET/POST /api/terminal`
> (`execution/web/api.py`), UI tab `VIEWS.terminal` (`dashboard/index.html`). Privileged setup:
> `deploy/sudoers-msp-ai-terminal.snippet`, `deploy/msp-ai.service.d/10-full-access.conf`,
> `deploy/msp-ai-recovery.service` (owner installs as root).

## Goal
Let a logged-in **admin** run shell commands on the host from a dashboard tab — a convenience over
opening SSH. This is the constitution's one explicit, **human-only** exception to Rule #6 ("no free-form
shell"). It is NOT available to the AI/agent loop: `dispatch()` and the registry are untouched; the agent
still has zero shell access.

## Interactive PTY (the terminal the UI actually uses)
The dashboard Terminal (and the :8091 recovery console) is a **real pseudo-terminal over a WebSocket**,
rendered with **xterm.js** — so `vim`, `top`, pagers, password prompts, colors, and **interactive
`claude`** all work. Ported from the donor design's design.
- `execution/web/wsutil.py` — minimal RFC-6455 handshake + frame codec for the stdlib server (no deps).
- `execution/web/pty_session.py` — `pty.fork()` a login shell, bridge fd⟷socket with `select` in the
  connection thread; JSON frames `{in|resize|out|exit}`; `SIGKILL`s the child on disconnect (no orphans).
- Route `GET /ws/terminal` (admin-gated, `terminal_enabled()`, audited `action="terminal"`) in both
  `server.py` (`_ws_terminal`, runs as `msp-ai`) and `recovery.py` (runs as root). xterm assets are
  vendored at `dashboard/vendor/{xterm.js,xterm.css,addon-fit.js}`.
- The old one-shot `adminshell.py` + `POST /api/terminal` stays as an audited programmatic fallback; the
  UI no longer uses it. Everything below (guardrails, root, kill switch) applies to the PTY too.

## Flow (one-shot fallback `POST /api/terminal`)
```
admin types command → POST /api/terminal {command}
  → _require_admin(role)            (non-admins: 403; nav item hidden for them too)
  → terminal_enabled()?             (MSPAI_ADMIN_TERMINAL=0 → 403)
  → audit.record(action="terminal", actor, detail=command)   ← logged BEFORE it runs
  → AdminShell.run(user, command)   → bash -c <command> in the user's tracked cwd
  → {ok, stdout, stderr, exit_code, cwd}
```
`GET /api/terminal` returns `{enabled, cwd, user, host}` to seed the tab.

## Guardrails (enforced in code)
| Control | How |
|---|---|
| Admin-only | route gated by `_require_admin(role)`; nav item filtered to `ME.role==='admin'` (with `build`). |
| Audited | `audit.record(..., action="terminal", detail=command[:500])` runs **before** execution. Records, never blocks. |
| Kill switch | `MSPAI_ADMIN_TERMINAL=0` (or false/off/no) → `terminal_enabled()` false → tab + endpoint disabled (I-4). |
| Output cap | `MSPAI_TERMINAL_MAXOUT` (default 1 MB/stream) so a runaway command can't OOM the response. |
| Time limit | **none by default** (D-22, "no blocks"); set `MSPAI_TERMINAL_TIMEOUT=<sec>` to re-impose one. |

## Root (D-22)
Full root is the owner's explicit decision. The web app process stays `msp-ai`; root is reached **per
command via `sudo`**, enabled by two owner-installed pieces:
- `deploy/sudoers-msp-ai-terminal.snippet` → `msp-ai ALL=(ALL) NOPASSWD: ALL` in `/etc/sudoers.d/`.
- `deploy/msp-ai.service.d/10-full-access.conf` → relaxes the systemd sandbox. The base unit sets
  `NoNewPrivileges=true`, `RestrictSUIDSGID=true`, `ProtectSystem=strict` — **each blocks sudo** (no
  escalation, no setuid, read-only FS). The drop-in turns them off so `sudo` can actually change the box.
  Optional: skip it to keep the main app hardened and do root work only via the :8091 console.

## Failover / recovery console (:8091)
`deploy/msp-ai-recovery.service` runs `execution/web/recovery.py` — a **standalone, terminal-only** root
console on :8091 (login page + terminal only; **no dashboard, no app APIs**), as **root**. It reuses the
same `AuthStore` / `SessionSigner` (shared `.session_secret`) / `AdminShell` / `AuditStore`, so the same
admin login works and commands land in the same audit log. The page is fully self-contained (no `/vendor`
assets) so it works even if the main app is broken. Purpose: when a deploy breaks :8090 or `restart`
crashes it, :8091 is still up so you can log in and fix the box from a browser instead of SSH.
**Operational rule:** the deploy flow restarts ONLY `msp-ai`, **never** `msp-ai-recovery` — that is what
lets recovery keep running during a bad update. Restart it manually only after the main app is healthy.

**Self-restart button:** the :8091 bar has a *restart console* button (mirror of the :8090 "Restart
MSP AI" button, D-36) → `POST /restart`, admin-gated, **audited before it runs** (the process is about
to die). Because the service restarts ITSELF, the command is handed to PID1 via
`systemd-run --collect systemctl restart msp-ai-recovery` (no sudo — recovery already runs as root;
plain `systemctl` fallback if systemd-run is absent). The page overlays, polls `/whoami` until the new
process answers, then reloads — the session cookie survives (shared persistent `.session_secret`).
Unit name override: `MSPAI_RECOVERY_UNIT` (default `msp-ai-recovery`).

**Prompt colors (lesson):** the two terminals run as different users — :8090 as `msp-ai` (skeleton
`.bashrc` → green `user@host` / blue path prompt) and :8091 as `root`, which has **no** `~/.bashrc`, so
Ubuntu's `/etc/bash.bashrc` clobbers any env `PS1` with a monochrome one. Fix lives in
`pty_session.py`: when the child runs as root it exports a **self-removing `PROMPT_COMMAND`** that
installs the same Debian colored PS1 (green `\u@\h`, blue `\w` — identical to :8090) and unsets itself
before the first prompt.
This keeps the styling identical across both consoles without touching `/root` dotfiles.

## Behaviour / limits
- **Not an interactive PTY** — no `vim`, `top`, pagers, or programs that read stdin. Each command is a
  one-shot `bash -c`. Output (stdout+stderr) is returned after it exits or times out.
- **`cd` persists per user** (tracked in-memory, thread-safe) so it feels like a session — but only when
  `cd` is the whole command (no `&&`/`;`/`|`). Everything else runs in the tracked working directory.
  Env exports do NOT persist (fresh process each time).
- Output is rendered with `textContent` in the UI, so command output can never inject HTML/JS.

## Accepted residual risk (D-22, explicit owner decision)
With full root enabled, a stolen admin session, CSRF, or an XSS hole = **full root + total server /
all-client-data takeover** — and today the channel is **plain HTTP on the LAN (no TLS on the box yet)**,
so the session cookie also crosses the network in the clear. This is the accepted cost of the convenience.
Levers to reduce it later: put TLS in front; scope the sudoers grant; keep admin creds tight; or set
`MSPAI_ADMIN_TERMINAL=0` (kill switch) and fall back to SSH.

## Edge cases / lessons
- Tracked cwd vanished (deleted out from under us) → resets to the project base, never errors.
- Command that exits non-zero with no stderr → UI shows `(exit N)`.
- Timeout → `{exit_code: 124, stderr: "(timed out after 30s)"}`.
- `ThreadingHTTPServer` means a long command runs in its own thread and does not block the dashboard.

## Files manager (Files tab) — same human-only trust class as the Terminal
Admin-only file browser/editor over the whole filesystem, running AS THE SERVICE USER (`msp-ai`) —
unreadable/unwritable paths fail honestly with the OS error; root work belongs in the Terminal.
Never reachable by the agent loop (web-admin routes, not tools). Every MUTATION is audited
(`action="file_write|file_upload|file_chmod|file_delete|file_mkdir"`, plus `file_download`).
- `GET  /api/fs/list?path=`   dirs-first listing: name/path/dir/size/mode/mtime/hidden + parent + root shortcuts
- `GET  /api/fs/file?path=`   text preview (≤256 KB shown; binary detected and refused for preview)
- `GET  /api/fs/download?path=` raw bytes with Content-Disposition (streamed by server.py, not JSON)
- `POST /api/fs/save {path, content}`        text edit (overwrites)
- `POST /api/fs/upload {dir, name, content_b64}`  base64 upload, ≤25 MB, name sanitised (no separators)
- `POST /api/fs/mkdir {dir, name}`
- `POST /api/fs/chmod {path, mode}`          octal string, e.g. "755"
- `POST /api/fs/delete {path, recursive}`    file unlink; a directory requires recursive=true (UI demands
                                             the name typed back first)
UI: breadcrumb + root chips + hidden-files toggle; left pane = entries, right pane = preview/editor with
Save / Download / chmod / Delete. The Files tab is hidden from non-admin users (and the API 403s them).

## Terminal clipboard UX (both :8090 Terminal tab and :8091 recovery console)

xterm.js ships with no real copy/paste affordance — selecting text doesn't copy, and Ctrl+C is SIGINT.
Both terminals now wire a shared behavior (`wireTermClipboard` in `dashboard/index.html`; mirrored inline
as `wireClip` in `execution/web/recovery.py` — keep the two in sync):
- **Select → auto-copy** on mouse-up (the primary fix).
- **Ctrl+Shift+C / Ctrl+Insert** copy the selection; **Ctrl+Shift+V / Shift+Insert / right-click** paste;
  native **Ctrl+V** keeps working.
- **Context matters:** the secure-context Clipboard API is used when available (HTTPS/localhost), but the
  recovery console is plain HTTP on `:8091`, so copy falls back to a hidden-textarea `execCommand('copy')`
  and paste relies on the browser's native paste event / right-click menu. A brief "copied/pasted" hint
  flashes in the toolbar. No server/PTY change — purely a browser-side affordance.
