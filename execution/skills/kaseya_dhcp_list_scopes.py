"""List DHCP scopes on a Windows DHCP server (D-76; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_dhcp_list_scopes"
DESCRIPTION = ("List the IPv4 scopes on a Windows DHCP server: scope ID, name, address range, "
               "subnet mask, state (active/inactive), and lease duration. Read-only, but rides "
               "the command engine so it's approval-gated. Give the DHCP server's `server` "
               "(machine name/AgentId). Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_dhcp"
CATEGORY = "write"            # read-only in effect; runs a command on the endpoint (gated like all command tools)
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the DHCP server's machine name/AgentId"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, **_: Any):
    from . import _kaseya_common as k
    cmd = ("try { Import-Module DhcpServer; Get-DhcpServerv4Scope | Select-Object ScopeId,Name,"
           "StartRange,EndRange,SubnetMask,State,LeaseDuration | Format-Table -AutoSize | "
           "Out-String -Width 4096 } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "read-only — read the scope list with kaseya_command_output"
    return out
