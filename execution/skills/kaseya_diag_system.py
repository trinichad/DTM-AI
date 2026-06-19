"""System summary — OS, uptime, model, RAM, pending reboot (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_diag_system"
DESCRIPTION = ("A quick system summary for a machine: computer name, make/model, OS + version, "
               "RAM, last boot time, uptime, and whether a reboot is pending. Read-only. Give the "
               "`server`. Read the result with kaseya_command_output.")
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
    cmd = ("try { $os = Get-CimInstance Win32_OperatingSystem; "
           "$cs = Get-CimInstance Win32_ComputerSystem; $up = (Get-Date) - $os.LastBootUpTime; "
           "$pending = (Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component "
           "Based Servicing\\RebootPending') -or (Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\"
           "CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired'); "
           "\"Computer       : $($cs.Name)`nModel          : $($cs.Manufacturer) $($cs.Model)`n"
           "OS             : $($os.Caption) ($($os.Version))`n"
           "RAM (GB)       : $([math]::Round($cs.TotalPhysicalMemory/1GB,1))`n"
           "Last boot      : $($os.LastBootUpTime)`n"
           "Uptime         : $($up.Days)d $($up.Hours)h $($up.Minutes)m`n"
           "Reboot pending : $pending\" } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "read-only — read the summary with kaseya_command_output"
    return out
