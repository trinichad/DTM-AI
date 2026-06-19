"""Add a DHCP exclusion range — stop the server handing out a sub-range (D-76; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_dhcp_add_exclusion"
DESCRIPTION = ("Exclude an address range within a DHCP scope so the server won't hand those out "
               "(e.g. carve out IPs for static devices). Give the `server`, the `scope_id` "
               "(e.g. 192.168.1.0), and the `start_range` + `end_range` to exclude. Remove it "
               "later with kaseya_dhcp_remove_exclusion. Read the result with "
               "kaseya_command_output.")
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
        "start_range": {"type": "string", "description": "first IP to exclude"},
        "end_range": {"type": "string", "description": "last IP to exclude"},
    },
    "required": ["server", "scope_id", "start_range", "end_range"],
    "additionalProperties": False,
}


def run(ctx, server: str, scope_id: str, start_range: str, end_range: str, **_: Any):
    from . import _kaseya_common as k
    sid = str(scope_id or "").strip()
    if not k.is_ipv4(sid):
        return {"ok": False, "error": "scope_id must be the scope's network address, e.g. 192.168.1.0"}
    if not (k.is_ipv4(start_range) and k.is_ipv4(end_range)):
        return {"ok": False, "error": "start_range and end_range must both be valid IPv4 addresses"}
    cmd = ("try { Import-Module DhcpServer; Add-DhcpServerv4ExclusionRange -ScopeId " +
           k.ps_quote(sid) + " -StartRange " + k.ps_quote(start_range.strip()) + " -EndRange " +
           k.ps_quote(end_range.strip()) + "; 'OK: excluded " + start_range.strip() + " - " +
           end_range.strip() + "' } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["scope_id"] = sid
        out["range"] = f"{start_range.strip()} - {end_range.strip()}"
        out["note"] = "add submitted — confirm with kaseya_command_output"
    return out
