"""Service health — auto-start services that aren't running (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_diag_services"
DESCRIPTION = ("List the services set to start automatically that are NOT currently running (the "
               "usual cause of 'X stopped working after a reboot'), plus a total service count. "
               "Read-only. Give the `server`. Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_diag"
CATEGORY = "write"            # read-only in effect; runs a command on the endpoint
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine to inspect (name/AgentId)"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, **_: Any):
    from . import _kaseya_common as k
    cmd = ("try { $auto = Get-CimInstance Win32_Service | Where-Object { ($_.StartMode -eq 'Auto') "
           "-and ($_.State -ne 'Running') }; "
           "\"Auto-start services NOT running (\" + @($auto).Count + \"):`n\" + "
           "($auto | Select-Object Name,DisplayName,State,StartMode | Sort-Object Name | "
           "Format-Table -AutoSize | Out-String -Width 4096) } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "read-only — read the service report with kaseya_command_output"
    return out
