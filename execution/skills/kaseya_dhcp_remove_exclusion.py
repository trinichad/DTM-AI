"""Remove a DHCP exclusion range (D-76; SOP: kaseya-vsa). Opposite of kaseya_dhcp_add_exclusion."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_dhcp_remove_exclusion"
DESCRIPTION = ("Remove a DHCP exclusion range so those addresses can be handed out again. Give "
               "the `server`, the `scope_id` (e.g. 192.168.1.0), and the `start_range` + "
               "`end_range` of the exclusion to remove (must match an existing exclusion). Read "
               "the result with kaseya_command_output.")
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
        "start_range": {"type": "string", "description": "first IP of the exclusion to remove"},
        "end_range": {"type": "string", "description": "last IP of the exclusion to remove"},
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
    cmd = ("try { Import-Module DhcpServer; Remove-DhcpServerv4ExclusionRange -ScopeId " +
           k.ps_quote(sid) + " -StartRange " + k.ps_quote(start_range.strip()) + " -EndRange " +
           k.ps_quote(end_range.strip()) + "; 'OK: removed exclusion " + start_range.strip() +
           " - " + end_range.strip() + "' } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["scope_id"] = sid
        out["range"] = f"{start_range.strip()} - {end_range.strip()}"
        out["note"] = "remove submitted — confirm with kaseya_command_output"
    return out
