"""Top processes by CPU/memory on a machine (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_diag_processes"
DESCRIPTION = ("Show the busiest processes on a machine — the top consumers by CPU and by memory "
               "('why is this box pegged?'). Read-only. Give the `server`. Read the result with "
               "kaseya_command_output.")
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
    cmd = ("try { $cpu = Get-Process | Sort-Object CPU -Descending | Select-Object -First 12 "
           "Name,Id,@{N='CPU(s)';E={[math]::Round($_.CPU,1)}},"
           "@{N='Mem(MB)';E={[math]::Round($_.WorkingSet64/1MB)}}; "
           "$mem = Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 12 "
           "Name,Id,@{N='Mem(MB)';E={[math]::Round($_.WorkingSet64/1MB)}}; "
           "\"=== TOP BY CPU ===`n\" + ($cpu | Format-Table -AutoSize | Out-String -Width 4096) + "
           "\"`n=== TOP BY MEMORY ===`n\" + ($mem | Format-Table -AutoSize | Out-String -Width 4096) } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "read-only — read the process list with kaseya_command_output"
    return out
