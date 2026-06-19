"""Create an Active Directory group — generic, any client (D-74; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_ad_create_group"
DESCRIPTION = ("Create an Active Directory GROUP on a domain controller (any client). Give the "
               "DC's machine name (`server`) and the group `name`. Optional: scope "
               "(global/universal/domainlocal, default global), category (security/distribution, "
               "default security), OU, description, and a different sAMAccountName. Skips if the "
               "group already exists. Nest it or add members with kaseya_ad_add_group_member. "
               "Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_ad"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

_SCOPES = {"global": "Global", "universal": "Universal", "domainlocal": "DomainLocal"}
_CATS = {"security": "Security", "distribution": "Distribution"}

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the domain controller's machine name/AgentId"},
        "name": {"type": "string", "description": "the group name"},
        "sam": {"type": "string", "description": "sAMAccountName (optional; defaults to the name)"},
        "scope": {"type": "string", "enum": list(_SCOPES), "description": "group scope (default global)"},
        "category": {"type": "string", "enum": list(_CATS),
                     "description": "security or distribution (default security)"},
        "ou": {"type": "string", "description": "target OU distinguished name (optional)"},
        "description": {"type": "string", "description": "group description (optional)"},
    },
    "required": ["server", "name"],
    "additionalProperties": False,
}


def run(ctx, server: str, name: str, sam: str = "", scope: str = "global",
        category: str = "security", ou: str = "", description: str = "", **_: Any):
    from . import _kaseya_common as k
    nm = k.clean_text(name, 256)
    if not nm:
        return {"ok": False, "error": "give a group name"}
    sam_v = k.clean_text(sam, 256) if (sam or "").strip() else nm
    gscope = _SCOPES.get((scope or "global").strip().lower())
    gcat = _CATS.get((category or "security").strip().lower())
    if not gscope:
        return {"ok": False, "error": "scope must be one of: global, universal, domainlocal"}
    if not gcat:
        return {"ok": False, "error": "category must be one of: security, distribution"}

    parts = ["New-ADGroup", "-Name", k.ps_quote(nm), "-SamAccountName", k.ps_quote(sam_v),
             "-GroupScope", gscope, "-GroupCategory", gcat]
    if (ou or "").strip():
        ou_v = k.clean_text(ou, 512)
        if not ou_v:
            return {"ok": False, "error": "the OU is not valid"}
        parts += ["-Path", k.ps_quote(ou_v)]
    if (description or "").strip():
        d = k.clean_text(description, 1024)
        if not d:
            return {"ok": False, "error": "the description is not valid"}
        parts += ["-Description", k.ps_quote(d)]

    cmd = ("try { Import-Module ActiveDirectory; "
           "$exists = $true; try { Get-ADGroup -Identity " + k.ps_quote(sam_v) +
           " -ErrorAction Stop | Out-Null } catch { $exists = $false }; "
           "if ($exists) { 'SKIP: group already exists' } else { " + " ".join(parts) +
           "; 'OK: group created' } } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["group"] = nm
        out["scope"] = gscope
        out["category"] = gcat
        out["note"] = "create submitted — confirm with kaseya_command_output (skips if it exists)"
    return out
