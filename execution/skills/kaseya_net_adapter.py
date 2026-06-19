"""List or control a network adapter on a machine (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_net_adapter"
DESCRIPTION = ("List a machine's network adapters, or enable/disable/restart one by name. Give the "
               "`server` and an `action` (list, enable, disable, restart); name the adapter for "
               "anything but list. WARNING: disabling the adapter a machine is reached through "
               "will take it offline — use with care on remote machines. Read the result with "
               "kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_net"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_ADAPTER_RE = re.compile(r'^[A-Za-z0-9 ._#()-]{1,128}$')
_ACTIONS = ("list", "enable", "disable", "restart")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine (name/AgentId)"},
        "action": {"type": "string", "enum": list(_ACTIONS), "description": "list/enable/disable/restart"},
        "name": {"type": "string", "description": "the adapter name (required unless action=list)"},
    },
    "required": ["server", "action"],
    "additionalProperties": False,
}


def run(ctx, server: str, action: str, name: str = "", **_: Any):
    from . import _kaseya_common as k
    act = (action or "").strip().lower()
    if act not in _ACTIONS:
        return {"ok": False, "error": "action must be list, enable, disable, or restart"}
    if act == "list":
        cmd = ("try { Get-NetAdapter | Select-Object Name,InterfaceDescription,Status,LinkSpeed,"
               "MacAddress | Format-Table -AutoSize | Out-String -Width 4096 } "
               "catch { 'ERROR: ' + $_.Exception.Message }")
    else:
        nm = (name or "").strip()
        if not _ADAPTER_RE.match(nm):
            return {"ok": False, "error": "give a valid adapter name"}
        verb = {"enable": "Enable-NetAdapter", "disable": "Disable-NetAdapter",
                "restart": "Restart-NetAdapter"}[act]
        confirm = "" if act == "restart" else " -Confirm:$false"
        cmd = ("try { " + verb + " -Name " + k.ps_quote(nm) + confirm + "; 'OK: " + act +
               " adapter' } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["action"] = act
        out["note"] = "submitted — confirm with kaseya_command_output"
    return out
