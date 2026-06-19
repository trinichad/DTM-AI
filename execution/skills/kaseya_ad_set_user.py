"""Modify an Active Directory user — any property or attribute (D-72; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

from . import _kaseya_common as _k

NAME = "kaseya_ad_set_user"
DESCRIPTION = ("Change an Active Directory user's properties on a domain controller — title, "
               "department, office, manager, phones, address, logon script, email, etc. ALSO "
               "sets ANY raw AD attribute (for AD/Entra hybrid changes): use set_attributes to "
               "replace a single-valued attribute, add_attributes/remove_attributes for "
               "multi-valued ones like proxyAddresses (e.g. add_attributes "
               "{'proxyAddresses':'smtp:alias@x.com'}), and clear_attributes to empty them. "
               "Read the result with kaseya_command_output.")
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
        **_k.AD_PROP_SCHEMA,
        "set_attributes": {"type": "object", "additionalProperties": True,
                           "description": "raw AD attributes to REPLACE (single-valued), e.g. "
                                          "{'extensionAttribute1':'x'} → Set-ADUser -Replace"},
        "add_attributes": {"type": "object", "additionalProperties": True,
                           "description": "raw multi-valued attributes to ADD a value to, e.g. "
                                          "{'proxyAddresses':'smtp:alias@x.com'} (value may be a "
                                          "list)"},
        "remove_attributes": {"type": "object", "additionalProperties": True,
                              "description": "raw multi-valued attributes to REMOVE a value from"},
        "clear_attributes": {"type": "array", "items": {"type": "string"},
                             "description": "attribute names to empty entirely"},
    },
    "required": ["server", "user"],
    "additionalProperties": False,
}

_ATTR_KEYS = ("set_attributes", "add_attributes", "remove_attributes", "clear_attributes")


def run(ctx, server: str, user: str, set_attributes: Any = None, add_attributes: Any = None,
        remove_attributes: Any = None, clear_attributes: Any = None, **kwargs: Any):
    import re
    from . import _kaseya_common as k
    u = k.clean_text(user, 256)
    if not u:
        return {"ok": False, "error": "give a valid AD user"}

    profile = {kk: vv for kk, vv in kwargs.items() if kk in k.AD_PROPS}
    frags, perr = k.ad_property_fragments(profile)
    if perr:
        return {"ok": False, "error": perr}
    parts = ["Set-ADUser", "-Identity", k.ps_quote(u)] + frags

    for op, attrs in (("-Replace", set_attributes), ("-Add", add_attributes),
                      ("-Remove", remove_attributes)):
        if isinstance(attrs, dict) and attrs:
            ht, e = k.ad_hashtable(attrs)
            if e:
                return {"ok": False, "error": e}
            parts += [op, ht]
    if isinstance(clear_attributes, list) and clear_attributes:
        names = [str(a).strip() for a in clear_attributes if str(a or "").strip()]
        for n in names:
            if not re.match(r"^[A-Za-z0-9-]+$", n):
                return {"ok": False, "error": f"invalid attribute name '{n}'"}
        if names:
            parts += ["-Clear", ",".join(names)]

    if len(parts) == 3:                          # only "Set-ADUser -Identity 'u'"
        return {"ok": False, "error": "give at least one property or attribute to change"}

    changed = [k.AD_PROPS[x] for x in profile if (kwargs.get(x) or "").strip()] \
        if isinstance(profile, dict) else []
    cmd = ("try { Import-Module ActiveDirectory; " + " ".join(parts) + "; "
           "Get-ADUser -Identity " + k.ps_quote(u) + " -Properties " +
           "Title,Department,Office,proxyAddresses,Manager | Select-Object Name,SamAccountName,"
           "Title,Department,Office,@{N='proxyAddresses';E={$_.proxyAddresses -join ', '}} | "
           "Format-List | Out-String } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["user"] = u
        ops = changed[:]
        for key in _ATTR_KEYS:
            v = locals().get(key)
            if v:
                ops.append(key)
        out["changing"] = ops
        out["note"] = "update submitted — confirm with kaseya_command_output"
    return out
