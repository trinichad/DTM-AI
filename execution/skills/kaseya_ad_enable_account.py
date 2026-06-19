"""Enable or disable an Active Directory account (D-72; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_ad_enable_account"
DESCRIPTION = ("Enable or DISABLE an Active Directory account on a domain controller (disabling "
               "is the common first step when offboarding someone). Pass the DC's machine name, "
               "the username, and enabled=true/false. Read the result with "
               "kaseya_command_output.")
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
        "enabled": {"type": "boolean",
                    "description": "true = enable the account, false = disable it"},
    },
    "required": ["server", "user", "enabled"],
    "additionalProperties": False,
}


def run(ctx, server: str, user: str, enabled: bool, **_: Any):
    from . import _kaseya_common as k
    u = k.clean_text(user, 256)
    if not u:
        return {"ok": False, "error": "give a valid AD user"}
    cmdlet = "Enable-ADAccount" if enabled else "Disable-ADAccount"
    cmd = ("try { Import-Module ActiveDirectory; " + cmdlet + " -Identity " + k.ps_quote(u) +
           "; Get-ADUser -Identity " + k.ps_quote(u) + " | Select-Object Name,SamAccountName,"
           "Enabled | Format-List | Out-String } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["user"] = u
        out["action"] = "enable" if enabled else "disable"
        out["note"] = f"account {'enable' if enabled else 'disable'} submitted — confirm with " \
                      f"kaseya_command_output"
    return out
