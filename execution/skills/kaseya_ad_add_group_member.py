"""Add an Active Directory user to a group (D-72; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_ad_add_group_member"
DESCRIPTION = ("Add an Active Directory user to a security/distribution GROUP on a domain "
               "controller. Pass the DC's machine name, the group name, and the username. Read "
               "the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_ad"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the domain controller's machine name/AgentId"},
        "group": {"type": "string", "description": "the AD group name (or its DN)"},
        "user": {"type": "string", "description": "the AD user's sAMAccountName to add"},
    },
    "required": ["server", "group", "user"],
    "additionalProperties": False,
}


def run(ctx, server: str, group: str, user: str, **_: Any):
    from . import _kaseya_common as k
    g = k.clean_text(group, 256)
    u = k.clean_text(user, 256)
    if not (g and u):
        return {"ok": False, "error": "give a valid group and user"}
    cmd = ("try { Import-Module ActiveDirectory; Add-ADGroupMember -Identity " + k.ps_quote(g) +
           " -Members " + k.ps_quote(u) + "; 'Added " + u.replace("'", "") + " to " +
           g.replace("'", "") + "' } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["group"] = g
        out["member_added"] = u
        out["note"] = "add submitted — confirm with kaseya_command_output"
    return out
