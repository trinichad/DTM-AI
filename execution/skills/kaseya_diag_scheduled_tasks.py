"""Non-Microsoft scheduled tasks + last run result (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_diag_scheduled_tasks"
DESCRIPTION = ("List the (non-Microsoft) scheduled tasks on a machine with their state, last run "
               "time, and last result code — handy for finding a backup/maintenance job that's "
               "failing. Read-only. Give the `server`. Read the result with kaseya_command_output.")
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
    cmd = ("try { Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\\Microsoft\\*' } | "
           "ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; [PSCustomObject]@{ "
           "Name=$_.TaskName; Path=$_.TaskPath; State=[string]$_.State; LastRun=$i.LastRunTime; "
           "Result=$i.LastTaskResult } } | Sort-Object Path,Name | Format-Table -AutoSize | "
           "Out-String -Width 4096 } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "read-only — read the task list with kaseya_command_output (Result 0 = OK)"
    return out
