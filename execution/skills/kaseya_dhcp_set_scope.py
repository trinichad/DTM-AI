"""Adjust a DHCP scope — name, state, range, lease duration (D-76; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_dhcp_set_scope"
DESCRIPTION = ("Adjust an existing DHCP scope on a Windows DHCP server. Give the `server` and "
               "`scope_id` (e.g. 192.168.1.0), then any of: name, state (active/inactive), "
               "lease_duration_days, the address range (start_range + end_range, both together), "
               "and description. Only the fields you pass are changed. Read the result with "
               "kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_dhcp"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_STATES = {"active": "Active", "inactive": "Inactive"}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the DHCP server's machine name/AgentId"},
        "scope_id": {"type": "string", "description": "the scope's network address, e.g. 192.168.1.0"},
        "name": {"type": "string", "description": "new scope name (optional)"},
        "state": {"type": "string", "enum": list(_STATES),
                  "description": "set the scope active or inactive (optional)"},
        "lease_duration_days": {"type": "integer", "minimum": 0, "maximum": 999,
                                "description": "lease duration in days (optional)"},
        "start_range": {"type": "string", "description": "new range start IP (pass with end_range)"},
        "end_range": {"type": "string", "description": "new range end IP (pass with start_range)"},
        "description": {"type": "string", "description": "new scope description (optional)"},
    },
    "required": ["server", "scope_id"],
    "additionalProperties": False,
}


def run(ctx, server: str, scope_id: str, name: str = "", state: str = "",
        lease_duration_days: Any = None, start_range: str = "", end_range: str = "",
        description: str = "", **_: Any):
    from . import _kaseya_common as k
    sid = str(scope_id or "").strip()
    if not k.is_ipv4(sid):
        return {"ok": False, "error": "scope_id must be the scope's network address, e.g. 192.168.1.0"}

    parts = ["Set-DhcpServerv4Scope", "-ScopeId", k.ps_quote(sid)]
    if (name or "").strip():
        nm = k.clean_text(name, 256)
        if not nm:
            return {"ok": False, "error": "the name is not valid"}
        parts += ["-Name", k.ps_quote(nm)]
    if (state or "").strip():
        st = _STATES.get(state.strip().lower())
        if not st:
            return {"ok": False, "error": "state must be 'active' or 'inactive'"}
        parts += ["-State", st]
    if lease_duration_days is not None:
        try:
            days = int(lease_duration_days)
        except (TypeError, ValueError):
            return {"ok": False, "error": "lease_duration_days must be a whole number of days"}
        if not 0 <= days <= 999:
            return {"ok": False, "error": "lease_duration_days must be between 0 and 999"}
        parts += ["-LeaseDuration", f"(New-TimeSpan -Days {days})"]
    if (start_range or "").strip() or (end_range or "").strip():
        if not (k.is_ipv4(start_range) and k.is_ipv4(end_range)):
            return {"ok": False, "error": "give BOTH start_range and end_range as valid IPs"}
        parts += ["-StartRange", k.ps_quote(start_range.strip()),
                  "-EndRange", k.ps_quote(end_range.strip())]
    if (description or "").strip():
        d = k.clean_text(description, 1024)
        if not d:
            return {"ok": False, "error": "the description is not valid"}
        parts += ["-Description", k.ps_quote(d)]

    if len(parts) == 3:                          # only Set-DhcpServerv4Scope -ScopeId 'x'
        return {"ok": False, "error": "give at least one scope field to change"}

    cmd = ("try { Import-Module DhcpServer; " + " ".join(parts) + "; Get-DhcpServerv4Scope "
           "-ScopeId " + k.ps_quote(sid) + " | Select-Object ScopeId,Name,StartRange,EndRange,"
           "State,LeaseDuration | Format-List | Out-String } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["scope_id"] = sid
        out["note"] = "update submitted — confirm with kaseya_command_output"
    return out
