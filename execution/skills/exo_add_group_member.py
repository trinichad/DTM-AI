"""Add a member to an email group (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_add_group_member"
DESCRIPTION = ("Add a member to an EMAIL GROUP (distribution list, mail-enabled security group, "
               "or Microsoft 365 group). Pass the group's email address and the member's email "
               "address (find groups with exo_list_groups). Add MANY members to one group in ONE "
               "call by passing `members` (a list) instead of `member` — do NOT call this tool "
               "once per member. Verifies membership before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "group": {"type": "string", "description": "the group's email address (or exact name)"},
        "member": {"type": "string", "description": "the member's email address to add"},
        "members": {"type": "array", "items": {"type": "string"},
                    "description": "add MANY members to this group in ONE call — a list of "
                                   "member email addresses; results come back together. Use this "
                                   "instead of calling the tool once per member."},
    },
    "required": ["group"],
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


def run(ctx, group: str, member: str = "", members: Any = None, **_: Any):
    exo = ctx.client("exo")
    wanted = [m for m in (str(x).strip() for x in (members or [])) if m]
    if wanted:                                         # batch add (D-110) — ONE call, ONE approval
        results = [_one(ctx, exo, group, m) for m in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "group": (group or "").strip(),
                "members_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, exo, group, member)


def _one(ctx, exo, group: str, member: str) -> dict:
    from . import _exo_common as c
    group, member = (group or "").strip(), (member or "").strip()
    if "@" not in member or " " in member:
        return {"ok": False, "member": member,
                "error": f"'{member}' is not a valid member email address"}

    # Resolve which KIND of group this is — the add + verify cmdlets differ.
    dg = exo.invoke("Get-DistributionGroup", {"Identity": group})
    if not c.err(dg) and _rows(dg):
        kind, add_cmd, add_params, list_cmd, list_params = (
            "distribution", "Add-DistributionGroupMember",
            {"Identity": group, "Member": member},
            "Get-DistributionGroupMember", {"Identity": group, "ResultSize": 500})
    else:
        ug = exo.invoke("Get-UnifiedGroup", {"Identity": group})
        if c.err(ug) or not _rows(ug):
            return {"ok": False, "member": member,
                    "error": f"no email group '{group}' found — list them with exo_list_groups"}
        kind, add_cmd, add_params, list_cmd, list_params = (
            "microsoft365", "Add-UnifiedGroupLinks",
            {"Identity": group, "LinkType": "Members", "Links": [member]},
            "Get-UnifiedGroupLinks", {"Identity": group, "LinkType": "Members",
                                      "ResultSize": 500})

    before = exo.invoke(list_cmd, list_params)
    if not c.err(before) and _is_member(_rows(before), member):
        return {"ok": True, "group": group, "member": member, "kind": kind,
                "note": "already a member — nothing to do"}

    r = exo.invoke(add_cmd, add_params)
    if c.err(r):
        return {"ok": False, "member": member, "step": "add member", "kind": kind,
                "error": c.err(r)}

    after = exo.invoke(list_cmd, list_params)
    if c.err(after):
        return {"ok": False, "member": member, "step": "verify", "kind": kind,
                "error": f"member add returned no error but the member list could not be "
                         f"re-read — {c.err(after)}"}
    if not _is_member(_rows(after), member):
        return {"ok": False, "member": member, "step": "verify", "kind": kind,
                "error": f"{add_cmd} returned no error but '{member}' is not in the member "
                         f"list — check Exchange directly"}
    return {"ok": True, "group": group, "member_added": member, "kind": kind,
            "member_count": len(_rows(after))}
