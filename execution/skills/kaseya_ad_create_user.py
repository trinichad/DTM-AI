"""Create an Active Directory user on a domain controller (D-72; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

from . import _kaseya_common as _k       # for the shared profile-property schema

NAME = "kaseya_ad_create_user"
DESCRIPTION = ("Create an Active Directory user on a domain controller, with full profile "
               "details. Required: the DC's machine name (`server`), first name, last name, "
               "username (sAMAccountName). Optional: password (generated if omitted), OU, and "
               "any profile fields — title, department, office, manager, phones, address, logon "
               "script, email, etc. The account is enabled + must change password at next "
               "logon. To set hybrid attributes like proxyAddresses afterwards, use "
               "kaseya_ad_set_user. Read the result with kaseya_command_output.")
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
        "first_name": {"type": "string", "description": "first name"},
        "last_name": {"type": "string", "description": "last name"},
        "username": {"type": "string", "description": "the sAMAccountName (login name)"},
        "password": {"type": "string", "description": "initial password (optional — generated if "
                                                     "omitted)"},
        "ou": {"type": "string", "description": "target OU distinguished name (optional), e.g. "
                                                "'OU=Staff,DC=acme,DC=local'"},
        **_k.AD_PROP_SCHEMA,                  # title/department/office/manager/phones/address/…
    },
    "required": ["server", "first_name", "last_name", "username"],
    "additionalProperties": False,
}


def run(ctx, server: str, first_name: str, last_name: str, username: str,
        password: str = "", ou: str = "", **kwargs: Any):
    from . import _kaseya_common as k
    first = k.clean_text(first_name, 64)
    last = k.clean_text(last_name, 64)
    uname = k.clean_text(username, 64)
    if not (first and last and uname):
        return {"ok": False, "error": "first name, last name, and username are required"}
    pw_given = bool((password or "").strip())
    pw = k.clean_text(password, 128) if pw_given else k.gen_password()
    if pw_given and (not pw or len(pw) < 8):
        return {"ok": False, "error": "the password must be at least 8 characters"}
    display = k.clean_text(kwargs.get("display_name"), 256) or f"{first} {last}"

    parts = ["New-ADUser", "-Name", k.ps_quote(display),
             "-GivenName", k.ps_quote(first), "-Surname", k.ps_quote(last),
             "-SamAccountName", k.ps_quote(uname),
             "-AccountPassword (ConvertTo-SecureString " + k.ps_quote(pw) +
             " -AsPlainText -Force)",
             "-Enabled $true", "-ChangePasswordAtLogon $true"]
    ou_c = k.clean_text(ou, 512)
    if (ou or "").strip():
        if not ou_c:
            return {"ok": False, "error": "the OU is not valid"}
        parts += ["-Path", k.ps_quote(ou_c)]
    # display_name handled via -Name above; don't also pass -DisplayName from the same value
    prof = {kk: vv for kk, vv in kwargs.items() if kk != "display_name"}
    if kwargs.get("display_name"):
        prof["display_name"] = kwargs["display_name"]
    frags, perr = k.ad_property_fragments(prof)
    if perr:
        return {"ok": False, "error": perr}
    parts += frags

    cmd = ("try { Import-Module ActiveDirectory; " + " ".join(parts) + "; "
           "Get-ADUser -Identity " + k.ps_quote(uname) +
           " -Properties EmailAddress,Title,Department | Select-Object Name,SamAccountName,"
           "Enabled,Title,Department,DistinguishedName | Format-List | Out-String } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["created_user"] = uname
        out["display_name"] = display
        if not pw_given:
            out["initial_password"] = pw          # generated → surface once
        out["note"] = ("create submitted — confirm with kaseya_command_output. The password is "
                       "in the approved command + Kaseya log; the user must change it at first "
                       "logon.")
    return out
