"""Remove a DHCP reservation (D-76; SOP: kaseya-vsa). The opposite of kaseya_dhcp_add_reservation."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_dhcp_remove_reservation"
DESCRIPTION = ("Remove a DHCP reservation (free a pinned IP) in a scope. Give the `server`, the "
               "`scope_id` (e.g. 192.168.1.0), and the reserved `ip` to remove. Read the result "
               "with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_dhcp"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the DHCP server's machine name/AgentId"},
        "scope_id": {"type": "string", "description": "the scope's network address, e.g. 192.168.1.0"},
        "ip": {"type": "string", "description": "the reserved IP address to remove"},
    },
    "required": ["server", "scope_id", "ip"],
    "additionalProperties": False,
}


def run(ctx, server: str, scope_id: str, ip: str, **_: Any):
    from . import _kaseya_common as k
    sid = str(scope_id or "").strip()
    if not k.is_ipv4(sid):
        return {"ok": False, "error": "scope_id must be the scope's network address, e.g. 192.168.1.0"}
    if not k.is_ipv4(ip):
        return {"ok": False, "error": "ip must be a valid IPv4 address"}
    cmd = ("try { Import-Module DhcpServer; Remove-DhcpServerv4Reservation -ScopeId " +
           k.ps_quote(sid) + " -IPAddress " + k.ps_quote(ip.strip()) + " -Confirm:$false; "
           "'OK: removed reservation " + ip.strip() + "' } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["scope_id"] = sid
        out["ip"] = ip.strip()
        out["note"] = "remove submitted — confirm with kaseya_command_output"
    return out
