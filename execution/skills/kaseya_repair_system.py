"""Repair Windows system files — DISM + sfc (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_repair_system"
DESCRIPTION = ("Repair corrupted Windows system files on a machine: DISM /RestoreHealth then "
               "sfc /scannow. This can take 10-30 minutes — the result comes back when it "
               "finishes. Give the `server`. Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine (name/AgentId)"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, **_: Any):
    from . import _kaseya_common as k
    cmd = ("try { $d = (DISM /Online /Cleanup-Image /RestoreHealth | Out-String); "
           "$s = (sfc /scannow | Out-String); "
           "\"=== DISM ===`n\" + $d + \"`n=== SFC ===`n\" + $s } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = ("repair submitted — this runs for many minutes; read the result with "
                       "kaseya_command_output once it completes")
    return out
