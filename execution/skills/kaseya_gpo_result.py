"""Show the Group Policy actually applied to a machine — gpresult (D-77; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_gpo_result"
DESCRIPTION = ("Show which Group Policies are actually applied to a machine (RSOP / gpresult): the "
               "GPOs in effect for the computer, last refresh time, and any that were filtered "
               "out. Runs on the TARGET machine. Read-only, but rides the command engine so it's "
               "approval-gated. Give that machine's `server`. Read the result with "
               "kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_gpo"
CATEGORY = "write"            # read-only in effect; runs a command on the endpoint
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine to report on (name/AgentId)"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, **_: Any):
    from . import _kaseya_common as k
    cmd = ("try { (gpresult /r /scope computer 2>&1 | Out-String) } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "read-only — read the applied-policy report with kaseya_command_output"
    return out
