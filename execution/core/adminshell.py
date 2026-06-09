"""AdminShell — run shell commands on the host for ADMIN users, from the dashboard Terminal tab.

⚠ This is the constitution's explicit, owner-approved exception to Rule #6 ("no free-form shell"),
recorded as D-21. It exists so an admin can run quick commands on the box without opening SSH.
The exception is fenced by guardrails enforced in code, not prose:

  • ADMIN-ONLY   — the route is admin-gated; non-admin users never see the tab or reach the endpoint.
  • AUDITED      — every command is written to the append-only audit log (actor + command) BEFORE it
                   runs, so even a command that kills the process leaves a record.
  • UNPRIVILEGED — runs as the dtm-ai service user, NOT root (no sudo wired). Real containment is the
                   systemd sandbox: ProtectSystem=strict + ReadWritePaths=/opt/dtm-ai, so writes stay
                   confined to the app dir and the rest of the FS is read-only.
  • KILL SWITCH  — DTM_ADMIN_TERMINAL=0 disables it instantly (config, not code — invariant I-4).
  • BOUNDED      — per-command timeout + output cap; one fresh process per command.

NOT an interactive PTY: no vim/top/long-running interactive programs, no persistent environment.
`cd` is tracked per user so it FEELS like a session; every other command is a fresh `bash -c` run in
the tracked working directory. Stdlib only.
"""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

from .config import Config, get_config

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TIMEOUT = 30           # seconds per command
_MAX_OUTPUT = 100_000   # chars returned per stream (stdout / stderr each)
_CD_BREAKERS = ("&&", "||", ";", "|", "\n")


def terminal_enabled(cfg: Optional[Config] = None) -> bool:
    """Instant kill switch (I-4): DTM_ADMIN_TERMINAL=0/false/off disables the terminal entirely."""
    cfg = cfg or get_config()
    val = str(cfg.get("DTM_ADMIN_TERMINAL") or "1").strip().lower()
    return val not in ("0", "false", "off", "no")


class AdminShell:
    """Per-user command runner with a tracked working directory. Thread-safe (ThreadingHTTPServer)."""

    def __init__(self, base: Optional[str] = None) -> None:
        self.base = str(base or _PROJECT_ROOT)
        self._cwd: dict[str, str] = {}
        self._lock = threading.Lock()

    def _safe_base(self) -> str:
        return self.base if Path(self.base).is_dir() else "/"

    def cwd(self, user: str) -> str:
        with self._lock:
            cur = self._cwd.get(user) or self.base
        if not Path(cur).is_dir():               # tracked dir vanished → reset
            cur = self._safe_base()
            with self._lock:
                self._cwd[user] = cur
        return cur

    def run(self, user: str, command: str) -> dict[str, Any]:
        command = (command or "").strip()
        cwd = self.cwd(user)
        if not command:
            return {"ok": True, "stdout": "", "stderr": "", "exit_code": 0, "cwd": cwd}

        # Persist `cd` across commands so it behaves like a shell — but only when `cd` is the whole
        # command (no chaining), so we never mis-handle e.g. `cd x && make`.
        if command == "cd" or (command.startswith("cd ") and not any(b in command for b in _CD_BREAKERS)):
            target = os.path.expanduser(command[2:].strip() or self.base)
            new = target if os.path.isabs(target) else os.path.normpath(os.path.join(cwd, target))
            if Path(new).is_dir():
                with self._lock:
                    self._cwd[user] = new
                return {"ok": True, "stdout": "", "stderr": "", "exit_code": 0, "cwd": new}
            return {"ok": False, "stdout": "", "stderr": f"cd: {target}: No such file or directory",
                    "exit_code": 1, "cwd": cwd}

        try:
            p = subprocess.run(["bash", "-c", command], cwd=cwd, capture_output=True,
                               text=True, timeout=_TIMEOUT)
            return {"ok": p.returncode == 0, "stdout": p.stdout[:_MAX_OUTPUT],
                    "stderr": p.stderr[:_MAX_OUTPUT], "exit_code": p.returncode, "cwd": cwd}
        except subprocess.TimeoutExpired:
            return {"ok": False, "stdout": "", "stderr": f"(timed out after {_TIMEOUT}s)",
                    "exit_code": 124, "cwd": cwd}
        except (OSError, subprocess.SubprocessError) as e:
            return {"ok": False, "stdout": "", "stderr": f"(failed to run: {e})",
                    "exit_code": 1, "cwd": cwd}
