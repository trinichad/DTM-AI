"""List Group Policy Objects on a domain controller (D-77; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_gpo_list"
DESCRIPTION = ("List the Group Policy Objects in the domain: name, ID, status, and when each was "
               "created/modified. Read-only, but rides the command engine so it's approval-gated. "
               "Run against a domain controller (`server`). Read the result with "
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
        "server": {"type": "string", "description": "a domain controller's machine name/AgentId"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, **_: Any):
    from . import _kaseya_common as k
    cmd = ("try { Import-Module GroupPolicy; Get-GPO -All | Select-Object DisplayName,Id,GpoStatus,"
           "CreationTime,ModificationTime | Sort-Object DisplayName | Format-Table -AutoSize | "
           "Out-String -Width 4096 } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "read-only — read the GPO list with kaseya_command_output"
    return out
