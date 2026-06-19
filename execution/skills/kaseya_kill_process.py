"""Kill a process on a machine — by name or PID (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_kill_process"
DESCRIPTION = ("Force-stop a process on a machine, by `process` name (all matching) or by `pid` "
               "(one). Give the `server` and one of the two. Read the result with "
               "kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_NAME_RE = re.compile(r'^[A-Za-z0-9 ._-]{1,128}$')
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine (name/AgentId)"},
        "process": {"type": "string", "description": "process name, e.g. 'chrome' (no .exe needed)"},
        "pid": {"type": "integer", "minimum": 1, "maximum": 4294967295,
                "description": "a specific process ID instead of a name"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, process: str = "", pid: Any = None, **_: Any):
    from . import _kaseya_common as k
    if pid is not None:
        try:
            pid_v = int(pid)
        except (TypeError, ValueError):
            return {"ok": False, "error": "pid must be a number"}
        if pid_v < 1:
            return {"ok": False, "error": "pid must be positive"}
        cmd = ("try { Stop-Process -Id " + str(pid_v) + " -Force -ErrorAction Stop; "
               "'OK: killed PID " + str(pid_v) + "' } catch { 'ERROR: ' + $_.Exception.Message }")
        target = f"PID {pid_v}"
    elif (process or "").strip():
        name = process.strip()
        if name.lower().endswith(".exe"):
            name = name[:-4]
        if not _NAME_RE.match(name):
            return {"ok": False, "error": "the process name has invalid characters"}
        cmd = ("try { Stop-Process -Name " + k.ps_quote(name) + " -Force -ErrorAction Stop; "
               "'OK: killed " + name + "' } catch { 'ERROR: ' + $_.Exception.Message }")
        target = name
    else:
        return {"ok": False, "error": "give a process name or a pid"}
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["target"] = target
        out["note"] = "kill submitted — confirm with kaseya_command_output"
    return out
