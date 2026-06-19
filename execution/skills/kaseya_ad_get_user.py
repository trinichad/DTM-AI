"""Look up an Active Directory user on a domain controller (D-72; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_ad_get_user"
DESCRIPTION = ("Look up an Active Directory user on a domain controller — enabled/locked state, "
               "email, last logon, OU, group membership. Pass the DC's machine name as `server` "
               "and the user's sAMAccountName/UPN. Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_ad"
CATEGORY = "write"           # runs PowerShell on the DC (read-only query, but still gated)
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the domain controller's Kaseya machine "
                                                    "name or AgentId"},
        "user": {"type": "string", "description": "the AD user's sAMAccountName or UPN"},
    },
    "required": ["server", "user"],
    "additionalProperties": False,
}


def run(ctx, server: str, user: str, **_: Any):
    from . import _kaseya_common as k
    u = k.clean_text(user, 256)
    if not u:
        return {"ok": False, "error": "give a valid AD user (sAMAccountName or UPN)"}
    cmd = ("try { Import-Module ActiveDirectory; Get-ADUser -Identity " + k.ps_quote(u) +
           " -Properties EmailAddress,Enabled,LockedOut,LastLogonDate,MemberOf,"
           "DistinguishedName | Select-Object Name,SamAccountName,Enabled,LockedOut,"
           "EmailAddress,LastLogonDate,DistinguishedName,@{N='Groups';E={($_.MemberOf | "
           "ForEach-Object {($_ -split ',')[0] -replace 'CN='}) -join ', '}} | Format-List | "
           "Out-String } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["looking_up"] = u
        out["note"] = "AD lookup submitted — read the result with kaseya_command_output"
    return out
