"""Delete an email distribution group (D-65; SOP: exchange-online).
The opposite of exo_create_distribution_group."""
from __future__ import annotations

from typing import Any

NAME = "exo_delete_distribution_group"
DESCRIPTION = ("DELETE an email distribution group (distribution list). Removes the group and "
               "its membership list (members' own mailboxes are untouched). Verifies it's gone "
               "before reporting success. For a Microsoft 365 group use m365_delete_group.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "group": {"type": "string", "description": "the group's email address or name"},
    },
    "required": ["group"],
    "additionalProperties": False,
}


def run(ctx, group: str, **_: Any):
    from . import _exo_common as c
    group = (group or "").strip()
    exo = ctx.client("exo")
    cur = exo.invoke("Get-DistributionGroup", {"Identity": group})
    rows = [x for x in (cur if isinstance(cur, list) else [cur]) if isinstance(x, dict)]
    if c.err(cur) or not rows:
        return {"ok": True, "group": group, "note": "no such distribution group — nothing "
                                                     "to delete"}
    name = str(rows[0].get("PrimarySmtpAddress") or rows[0].get("Name") or group)
    r = exo.invoke("Remove-DistributionGroup", {"Identity": group, "Confirm": False})
    if c.err(r):
        return {"ok": False, "step": "delete", "error": c.err(r)}
    check = exo.invoke("Get-DistributionGroup", {"Identity": group})
    rows2 = [x for x in (check if isinstance(check, list) else [check]) if isinstance(x, dict)]
    if not c.err(check) and rows2:
        return {"ok": False, "step": "verify",
                "error": f"Remove-DistributionGroup returned no error but '{group}' still "
                         f"exists — check Exchange directly"}
    return {"ok": True, "deleted": name}
