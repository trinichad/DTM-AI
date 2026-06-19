"""Reset an Active Directory user's password (D-72; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_ad_reset_password"
DESCRIPTION = ("Reset an Active Directory user's password on a domain controller. Pass the DC's "
               "machine name, the username, and optionally a new password (a strong one is "
               "generated if omitted). By default the user must change it at next logon. Read "
               "the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_ad"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the domain controller's machine name/AgentId"},
        "user": {"type": "string", "description": "the AD user's sAMAccountName or UPN"},
        "password": {"type": "string", "description": "new password (optional — generated if "
                                                     "omitted)"},
        "must_change": {"type": "boolean",
                        "description": "require a change at next logon (default true)"},
    },
    "required": ["server", "user"],
    "additionalProperties": False,
}


def run(ctx, server: str, user: str, password: str = "", must_change: bool = True, **_: Any):
    from . import _kaseya_common as k
    u = k.clean_text(user, 256)
    if not u:
        return {"ok": False, "error": "give a valid AD user"}
    pw_given = bool((password or "").strip())
    pw = k.clean_text(password, 128) if pw_given else k.gen_password()
    if pw_given and (not pw or len(pw) < 8):
        return {"ok": False, "error": "the password must be at least 8 characters"}
    change = (f"Set-ADUser -Identity {k.ps_quote(u)} -ChangePasswordAtLogon $true; "
              if must_change else "")
    cmd = ("try { Import-Module ActiveDirectory; Set-ADAccountPassword -Identity " +
           k.ps_quote(u) + " -Reset -NewPassword (ConvertTo-SecureString " + k.ps_quote(pw) +
           " -AsPlainText -Force); " + change + "'Password reset for " + u.replace("'", "") +
           "' } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["reset_user"] = u
        if not pw_given:
            out["new_password"] = pw
        out["note"] = ("reset submitted — confirm with kaseya_command_output. The password is "
                       "in the approved command + Kaseya log.")
    return out
