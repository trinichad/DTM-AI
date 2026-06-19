"""Restart a Windows service on a machine (D-71; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_restart_service"
DESCRIPTION = ("Restart a Windows SERVICE on a machine (e.g. the print spooler, a line-of-"
               "business service). Pass the machine and the service name (the Windows service "
               "name, e.g. 'Spooler', or its display name). Runs through the Command Toolkit "
               "engine; confirm with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
        "service": {"type": "string", "description": "the service name or display name"},
    },
    "required": ["machine", "service"],
    "additionalProperties": False,
}

_SVC = re.compile(r"^[A-Za-z0-9 ._()-]+$")            # service names — no shell metacharacters


def run(ctx, machine: str, service: str, **_: Any):
    from . import _kaseya_common as k
    service = (service or "").strip()
    if not _SVC.match(service):
        return {"ok": False, "error": f"'{service}' is not a valid service name"}
    esc = service.replace("'", "''")
    command = (f"Restart-Service -DisplayName '{esc}' -Force -ErrorAction SilentlyContinue; "
               f"Restart-Service -Name '{esc}' -Force -ErrorAction SilentlyContinue; "
               f"Get-Service -Name '{esc}','{esc}' -ErrorAction SilentlyContinue | "
               f"Select-Object Name,Status | Format-Table -AutoSize | Out-String")
    out = k.run_command(ctx, machine, command)
    if out.get("ok"):
        out["restarting_service"] = service
        out["note"] = "service restart submitted — confirm status with kaseya_command_output"
    return out
