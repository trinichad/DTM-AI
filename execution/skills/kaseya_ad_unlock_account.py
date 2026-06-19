"""Unlock a locked-out Active Directory account (D-72; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_ad_unlock_account"
DESCRIPTION = ("Unlock a locked-out Active Directory account on a domain controller. Pass the "
               "DC's machine name and the username. Read the result with kaseya_command_output.")
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
        "user": {"type": "string", "description": "the AD user's sAMAccountName or UPN"},
    },
    "required": ["server", "user"],
    "additionalProperties": False,
}


def run(ctx, server: str, user: str, **_: Any):
    from . import _kaseya_common as k
    u = k.clean_text(user, 256)
    if not u:
        return {"ok": False, "error": "give a valid AD user"}
    cmd = ("try { Import-Module ActiveDirectory; Unlock-ADAccount -Identity " + k.ps_quote(u) +
           "; Get-ADUser -Identity " + k.ps_quote(u) + " -Properties LockedOut | "
           "Select-Object Name,SamAccountName,LockedOut | Format-List | Out-String } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["unlocked_user"] = u
        out["note"] = "unlock submitted — confirm with kaseya_command_output"
    return out
