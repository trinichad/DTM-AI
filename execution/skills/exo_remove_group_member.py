"""Remove a member from an email group (D-65; SOP: exchange-online).
The opposite of exo_add_group_member."""
from __future__ import annotations

from typing import Any

NAME = "exo_remove_group_member"
DESCRIPTION = ("Remove a member from an EMAIL GROUP (distribution list, mail-enabled security "
               "group, or Microsoft 365 group). Find groups with exo_list_groups. Verifies the "
               "member is gone before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "group": {"type": "string", "description": "the group's email address (or exact name)"},
        "member": {"type": "string", "description": "the member's email address to remove"},
    },
    "required": ["group", "member"],
    "additionalProperties": False,
}


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def _is_member(rows: list[dict], member: str) -> bool:
    m = member.lower()
    for row in rows:
        for f in ("PrimarySmtpAddress", "WindowsLiveID", "Name", "Alias"):
            if str(row.get(f) or "").lower() == m:
                return True
    return False


def run(ctx, group: str, member: str, **_: Any):
    from . import _exo_common as c
    group, member = (group or "").strip(), (member or "").strip()
    if "@" not in member:
        return {"ok": False, "error": f"'{member}' is not a valid member email address"}
    exo = ctx.client("exo")

    dg = exo.invoke("Get-DistributionGroup", {"Identity": group})
    if not c.err(dg) and _rows(dg):
        kind, rm_cmd, rm_params, list_cmd, list_params = (
            "distribution", "Remove-DistributionGroupMember",
            {"Identity": group, "Member": member, "Confirm": False,
             "BypassSecurityGroupManagerCheck": True},
            "Get-DistributionGroupMember", {"Identity": group, "ResultSize": 500})
    else:
        ug = exo.invoke("Get-UnifiedGroup", {"Identity": group})
        if c.err(ug) or not _rows(ug):
            return {"ok": False, "error": f"no email group '{group}' found — list them with "
                                          f"exo_list_groups"}
        kind, rm_cmd, rm_params, list_cmd, list_params = (
            "microsoft365", "Remove-UnifiedGroupLinks",
            {"Identity": group, "LinkType": "Members", "Links": [member], "Confirm": False},
            "Get-UnifiedGroupLinks", {"Identity": group, "LinkType": "Members",
                                      "ResultSize": 500})

    before = exo.invoke(list_cmd, list_params)
    if not c.err(before) and not _is_member(_rows(before), member):
        return {"ok": True, "group": group, "member": member, "kind": kind,
                "note": "not a member — nothing to remove"}

    r = exo.invoke(rm_cmd, rm_params)
    if c.err(r):
        return {"ok": False, "step": "remove member", "kind": kind, "error": c.err(r)}

    after = exo.invoke(list_cmd, list_params)
    if not c.err(after) and _is_member(_rows(after), member):
        return {"ok": False, "step": "verify", "kind": kind,
                "error": f"{rm_cmd} returned no error but '{member}' is still in the member "
                         f"list — check Exchange directly"}
    return {"ok": True, "group": group, "member_removed": member, "kind": kind}
