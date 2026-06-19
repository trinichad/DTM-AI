"""Create an email distribution group (D-56; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_create_distribution_group"
DESCRIPTION = ("Create an email DISTRIBUTION GROUP (distribution list) in the client's "
               "Exchange Online. Give the group's email address and a display name; members "
               "can be seeded now (their addresses) or added later with exo_add_group_member. "
               "For an Entra SECURITY group use m365_create_group. Verifies before reporting "
               "success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "email": {"type": "string", "description": "the group's email address, "
                                                   "e.g. team@demodomain.com"},
        "display_name": {"type": "string",
                         "description": "display name (defaults to the part before @)"},
        "members": {"type": "array", "items": {"type": "string"},
                    "description": "member email addresses to add at creation (optional)"},
    },
    "required": ["email"],
    "additionalProperties": False,
}


def run(ctx, email: str, display_name: str = "", members: Any = None, **_: Any):
    from . import _exo_common as c
    email = (email or "").strip().lower()
    if "@" not in email or " " in email:
        return {"ok": False, "error": f"'{email}' is not a valid email address"}
    name = (display_name or "").strip() or email.split("@")[0]
    seed = [str(m).strip() for m in (members or []) if str(m or "").strip()] \
        if isinstance(members, list) else []
    exo = ctx.client("exo")

    existing = exo.invoke("Get-DistributionGroup", {"Identity": email})
    if not c.err(existing):
        return {"ok": False, "error": f"a distribution group '{email}' already exists"}

    params: dict[str, Any] = {"Name": name, "DisplayName": name,
                              "PrimarySmtpAddress": email,
                              "Alias": email.split("@")[0], "Type": "Distribution",
                              "Confirm": False}
    if seed:
        params["Members"] = seed
    r = exo.invoke("New-DistributionGroup", params)
    if c.err(r):
        return {"ok": False, "step": "create", "error": c.err(r)}

    check = exo.invoke("Get-DistributionGroup", {"Identity": email})
    rows = [x for x in (check if isinstance(check, list) else [check]) if isinstance(x, dict)]
    if c.err(check) or not rows:
        return {"ok": False, "step": "verify",
                "error": f"New-DistributionGroup returned no error but '{email}' could not "
                         f"be read back — check Exchange directly"}
    return {"ok": True, "created": email, "display_name": name,
            **({"members_seeded": seed} if seed else {}),
            "note": "add more members with exo_add_group_member"}
