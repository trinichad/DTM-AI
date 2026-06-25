"""Add a member to a directory (Entra) group (D-56; SOP: m365-graph)."""
from __future__ import annotations

from typing import Any

NAME = "m365_add_security_group_member"
DESCRIPTION = ("Add a user to a DIRECTORY (Entra) group — security or Microsoft 365 group "
               "(find them with m365_list_entra_groups). Dynamic groups are refused: their "
               "members come from the membership rule only. For EMAIL distribution lists use "
               "exo_add_group_member instead. Add MANY members to one group in ONE call by "
               "passing `members` (a list) instead of `member` — do NOT call this tool once per "
               "member. Verifies membership before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"            # group membership often gates access to resources
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "group": {"type": "string", "description": "group name or id"},
        "member": {"type": "string", "description": "the user's sign-in address to add"},
        "members": {"type": "array", "items": {"type": "string"},
                    "description": "add MANY members to this group in ONE call — a list of "
                                   "sign-in addresses; results come back together. Use this "
                                   "instead of calling the tool once per member."},
    },
    "required": ["group"],
    "additionalProperties": False,
}


def describe_approval(ctx, args: dict):
    """Approval-card preview (D-90): resolve the group id → its display name so the owner confirms
    the RIGHT group, not a raw GUID. Read-only + best-effort — dispatch falls back to the raw args
    on any error, so this never blocks or alters the approval."""
    from . import _graph_common as g
    grp, _bad = g.resolve_group(ctx, str(args.get("group") or ""))
    name, gid = (grp or {}).get("displayName"), (grp or {}).get("id")
    return {"Add to group": (f"{name}  ·  {gid}" if name else str(args.get("group") or "")),
            "User": str(args.get("member") or "")}


def run(ctx, group: str, member: str = "", members: Any = None, **_: Any):
    wanted = [m for m in (str(x).strip() for x in (members or [])) if m]
    if wanted:                                         # batch add (D-110) — ONE call, ONE approval
        results = ctx.map_progress(wanted[:500], lambda m: _one(ctx, group, m))
        return {"ok": any(r.get("ok") for r in results), "group": group,
                "members_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, group, member)


def _one(ctx, group: str, member: str) -> dict:
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_write
    from . import _graph_common as g
    try:
        grp, bad = g.resolve_group(ctx, group)
        if bad:
            return bad
        if "DynamicMembership" in [str(t) for t in (grp.get("groupTypes") or [])]:
            return {"ok": False, "member": member, "error":
                    f"'{grp.get('displayName')}' is a DYNAMIC group — members are computed "
                    f"from its rule ({grp.get('membershipRule')}); they can't be added "
                    f"manually. Change the rule, or make the user match it."}
        uid, bad = g.resolve_user_id(ctx, member)
        if bad:
            return {**bad, "member": member}
        gid = str(grp.get("id"))
        if g.is_group_member(ctx, gid, uid):
            return {"ok": True, "group": grp.get("displayName"), "member": member,
                    "note": "already a member — nothing to do"}
        r = scoped_write(ctx, "m365", f"/groups/{gid}/members/$ref",
                         body={"@odata.id":
                               f"https://graph.microsoft.com/v1.0/directoryObjects/{uid}"},
                         method="POST")
        bad = g.fail(r)
        if bad:
            return {**bad, "member": member}
        # Membership reads are eventually-consistent — poll before declaring failure (D-104).
        verified, _ = g.settle(lambda: g.is_group_member(ctx, gid, uid), lambda m: m)
    except HttpError as exc:
        return g.err403(exc, "adding the member",
                        "Group.ReadWrite.All (and the signing admin needs a group-management "
                        "role, e.g. Groups Administrator)")

    if not verified:
        return {"ok": False, "member": member, "step": "verify", "pending": True,
                "error": "the add returned no error but Entra doesn't show the user in the group "
                         "yet — usually replication lag; re-check shortly rather than re-running"}
    return {"ok": True, "group": grp.get("displayName"), "group_id": gid,
            "member_added": member}
