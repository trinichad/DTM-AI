"""Add a member to a directory (Entra) group (D-56; SOP: m365-graph)."""
from __future__ import annotations

from typing import Any

NAME = "m365_add_security_group_member"
DESCRIPTION = ("Add a user to a DIRECTORY (Entra) group — security or Microsoft 365 group "
               "(find them with m365_list_entra_groups). Dynamic groups are refused: their "
               "members come from the membership rule only. For EMAIL distribution lists use "
               "exo_add_group_member instead. Verifies membership before reporting success.")
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
    },
    "required": ["group", "member"],
    "additionalProperties": False,
}


def run(ctx, group: str, member: str, **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_write
    from . import _graph_common as g
    try:
        grp, bad = g.resolve_group(ctx, group)
        if bad:
            return bad
        if "DynamicMembership" in [str(t) for t in (grp.get("groupTypes") or [])]:
            return {"ok": False, "error":
                    f"'{grp.get('displayName')}' is a DYNAMIC group — members are computed "
                    f"from its rule ({grp.get('membershipRule')}); they can't be added "
                    f"manually. Change the rule, or make the user match it."}
        uid, bad = g.resolve_user_id(ctx, member)
        if bad:
            return bad
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
            return bad
        verified = g.is_group_member(ctx, gid, uid)
    except HttpError as exc:
        return g.err403(exc, "adding the member",
                        "Group.ReadWrite.All (and the signing admin needs a group-management "
                        "role, e.g. Groups Administrator)")

    if not verified:
        return {"ok": False, "step": "verify",
                "error": "the add returned no error but the user is not in the member "
                         "list — check Entra directly"}
    return {"ok": True, "group": grp.get("displayName"), "group_id": gid,
            "member_added": member}
