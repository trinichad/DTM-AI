"""Show a DHCP scope's utilization — free vs in-use IPs (D-76; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_dhcp_scope_stats"
DESCRIPTION = ("Show a DHCP scope's utilization: how many addresses are FREE (available), in use, "
               "reserved, and the percent used. Answers 'how many IPs are left in this scope?'. "
               "Read-only, but rides the command engine so it's approval-gated. Give the DHCP "
               "`server` and the `scope_id` (the scope's network address, e.g. 192.168.1.0). Read "
               "the result with kaseya_command_output.")
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
    cmd = ("try { Import-Module DhcpServer; Get-DhcpServerv4ScopeStatistics -ScopeId " +
           k.ps_quote(sid) + " | Select-Object ScopeId,Free,InUse,Reserved,PercentageInUse | "
           "Format-List | Out-String } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["scope_id"] = sid
        out["note"] = "read-only — 'Free' is the available IP count; read it with kaseya_command_output"
    return out
