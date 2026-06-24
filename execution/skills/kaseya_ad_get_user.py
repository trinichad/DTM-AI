"""Look up an Active Directory user on a domain controller (D-72; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_ad_get_user"
DESCRIPTION = ("Look up Active Directory user(s) on a domain controller — enabled/locked state, "
               "email, last logon, OU, group membership. Pass the DC's machine name as `server` "
               "and the user's sAMAccountName/UPN as `user`; or pass `users` (a list) to look up "
               "MANY in ONE job — do NOT call this tool once per user. Read the result with "
               "kaseya_command_output.")
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
        "users": {"type": "array", "items": {"type": "string"},
                  "description": "look up MANY users in ONE job — a list of sAMAccountNames / UPNs; "
                                 "one PowerShell run, one approval, one output. Use this instead "
                                 "of calling the tool once per user."},
    },
    "required": ["server"],
    "additionalProperties": False,
}

_PROPS = "EmailAddress,Enabled,LockedOut,LastLogonDate,MemberOf,DistinguishedName"
_SELECT = ("Name,SamAccountName,Enabled,LockedOut,EmailAddress,LastLogonDate,DistinguishedName,"
           "@{N='Groups';E={($_.MemberOf | ForEach-Object {($_ -split ',')[0] -replace 'CN='}) "
           "-join ', '}}")


def _pipeline(identity_expr: str) -> str:
    return (f"Get-ADUser -Identity {identity_expr} -Properties {_PROPS} | "
            f"Select-Object {_SELECT} | Format-List | Out-String")


def run(ctx, server: str, user: str = "", users: Any = None, **_: Any):
    from . import _kaseya_common as k
    wanted = [u for u in (k.clean_text(x, 256) for x in (users or [])) if u]
    if wanted:                                         # batch lookup (D-110) — one PS job, one approval
        array = ", ".join(k.ps_quote(u) for u in wanted)
        cmd = ("Import-Module ActiveDirectory; @(" + array + ") | ForEach-Object { $u = $_; "
               "try { " + _pipeline("$u") + " } catch { 'ERROR for ' + $u + ': ' + "
               "$_.Exception.Message } }")
        out = k.run_command(ctx, server, cmd)
        if out.get("ok"):
            out["looking_up"] = wanted
            out["note"] = (f"AD batch lookup submitted ({len(wanted)} users) — read the result "
                           f"with kaseya_command_output")
        return out

    u = k.clean_text(user, 256)
    if not u:
        return {"ok": False, "error": "give a valid AD user (sAMAccountName or UPN)"}
    cmd = ("try { Import-Module ActiveDirectory; " + _pipeline(k.ps_quote(u)) +
           " } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["looking_up"] = u
        out["note"] = "AD lookup submitted — read the result with kaseya_command_output"
    return out
