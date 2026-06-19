"""List DHCP reservations in a scope (D-76; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_dhcp_list_reservations"
DESCRIPTION = ("List the DHCP reservations in a scope: reserved IP, MAC (ClientId), name, and "
               "description. Read-only, but rides the command engine so it's approval-gated. Give "
               "the DHCP `server` and the `scope_id` (e.g. 192.168.1.0). Read the result with "
               "kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_dhcp"
CATEGORY = "write"            # read-only in effect; runs a command on the endpoint
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the DHCP server's machine name/AgentId"},
        "scope_id": {"type": "string", "description": "the scope's network address, e.g. 192.168.1.0"},
    },
    "required": ["server", "scope_id"],
    "additionalProperties": False,
}


def run(ctx, server: str, scope_id: str, **_: Any):
    from . import _kaseya_common as k
    sid = str(scope_id or "").strip()
    if not k.is_ipv4(sid):
        return {"ok": False, "error": "scope_id must be the scope's network address, e.g. 192.168.1.0"}
    cmd = ("try { Import-Module DhcpServer; Get-DhcpServerv4Reservation -ScopeId " + k.ps_quote(sid) +
           " | Select-Object IPAddress,ClientId,Name,Description | Format-Table -AutoSize | "
           "Out-String -Width 4096 } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["scope_id"] = sid
        out["note"] = "read-only — read the reservation list with kaseya_command_output"
    return out
