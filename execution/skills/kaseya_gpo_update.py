"""Force a Group Policy refresh on a machine — gpupdate /force (D-77; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_gpo_update"
DESCRIPTION = ("Force a Group Policy refresh on a machine (gpupdate /force) so newly-changed "
               "policies apply now instead of at the next cycle. Runs on the TARGET machine. Give "
               "that machine's `server`. Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_gpo"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine to refresh (name/AgentId)"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, **_: Any):
    from . import _kaseya_common as k
    # /target:computer avoids the interactive logoff/reboot prompt that /force can trigger for user policy
    cmd = ("try { (gpupdate /target:computer /force 2>&1 | Out-String) } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "gpupdate submitted — confirm with kaseya_command_output"
    return out
