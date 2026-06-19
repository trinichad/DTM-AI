"""Start/stop a service and/or set its startup type (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_service_control"
DESCRIPTION = ("Control a Windows service on a machine: `action` start/stop/restart, and/or set "
               "`start_type` (automatic, manual, disabled). Give the `server` and the `service` "
               "name. (To just restart, kaseya_restart_service also works.) Read the result with "
               "kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_SVC_RE = re.compile(r'^[A-Za-z0-9 ._-]{1,256}$')
_ACTIONS = {"start": "Start-Service", "stop": "Stop-Service", "restart": "Restart-Service"}
_START = {"automatic": "Automatic", "manual": "Manual", "disabled": "Disabled"}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine (name/AgentId)"},
        "service": {"type": "string", "description": "the service name (short name or display name)"},
        "action": {"type": "string", "enum": list(_ACTIONS), "description": "start/stop/restart (optional)"},
        "start_type": {"type": "string", "enum": list(_START),
                       "description": "set startup type (optional)"},
    },
    "required": ["server", "service"],
    "additionalProperties": False,
}


def run(ctx, server: str, service: str, action: str = "", start_type: str = "", **_: Any):
    from . import _kaseya_common as k
    svc = (service or "").strip()
    if not _SVC_RE.match(svc):
        return {"ok": False, "error": "the service name has invalid characters"}
    act = (action or "").strip().lower()
    stp = (start_type or "").strip().lower()
    if not act and not stp:
        return {"ok": False, "error": "give an action and/or a start_type"}
    if act and act not in _ACTIONS:
        return {"ok": False, "error": "action must be start, stop, or restart"}
    if stp and stp not in _START:
        return {"ok": False, "error": "start_type must be automatic, manual, or disabled"}

    steps = []
    if stp:
        steps.append("Set-Service -Name " + k.ps_quote(svc) + " -StartupType " + _START[stp])
    if act:
        force = " -Force" if act in ("stop", "restart") else ""
        steps.append(_ACTIONS[act] + " -Name " + k.ps_quote(svc) + force)
    body = "; ".join(steps)
    cmd = ("try { " + body + "; (Get-Service -Name " + k.ps_quote(svc) +
           " | Select-Object Name,Status,StartType | Format-List | Out-String) } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["service"] = svc
        out["note"] = "submitted — confirm with kaseya_command_output"
    return out
