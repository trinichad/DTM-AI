"""Remove a member from a directory (Entra) group (D-65; SOP: m365-graph).
The opposite of m365_add_security_group_member."""
from __future__ import annotations

from typing import Any

NAME = "m365_remove_security_group_member"
DESCRIPTION = ("Remove a user from a DIRECTORY (Entra) group — security or Microsoft 365 group. "
               "Dynamic groups are refused (their membership comes from the rule). For an EMAIL "
               "distribution list use exo_remove_group_member. Verifies removal before "
               "reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "group": {"type": "string", "description": "group name or id"},
        "member": {"type": "string", "description": "the user's sign-in address to remove"},
    },
    "required": ["group", "member"],
    "additionalProperties": False,
}


def describe_approval(ctx, args: dict):
    """Approval-card preview (D-90): resolve the group id → its display name so the owner confirms
    the RIGHT group, not a raw GUID. Read-only + best-effort — dispatch falls back to the raw args
    on any error, so this never blocks or alters the approval."""
    from . import _graph_common as g
    grp, _bad = g.resolve_group(ctx, str(args.get("group") or ""))
    name, gid = (grp or {}).get("displayName"), (grp or {}).get("id")
    return {"Remove from group": (f"{name}  ·  {gid}" if name else str(args.get("group") or "")),
            "User": str(args.get("member") or "")}


def run(ctx, group: str, member: str, **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_delete
    from . import _graph_common as g
    try:
        grp, bad = g.resolve_group(ctx, group)
        if bad:
            return bad
        if "DynamicMembership" in [str(t) for t in (grp.get("groupTypes") or [])]:
            return {"ok": False, "error":
                    f"'{grp.get('displayName')}' is a DYNAMIC group — membership comes from its "
                    f"rule; members can't be removed manually."}
        uid, bad = g.resolve_user_id(ctx, member)
        if bad:
            return bad
        gid = str(grp.get("id"))
        if not g.is_group_member(ctx, gid, uid):
            return {"ok": True, "group": grp.get("displayName"), "member": member,
                    "note": "not a member — nothing to remove"}
        r = scoped_delete(ctx, "m365", f"/groups/{gid}/members/{uid}/$ref")
        bad = g.fail(r)
        if bad:
            return bad
        still = g.is_group_member(ctx, gid, uid)
    except HttpError as exc:
        return g.err403(exc, "removing the member",
                        "Group.ReadWrite.All (and the signing admin needs a group-management "
                        "role, e.g. Groups Administrator)")
    if still:
        return {"ok": False, "step": "verify",
                "error": "the remove returned no error but the user is still in the member "
                         "list — check Entra directly"}
    return {"ok": True, "group": grp.get("displayName"), "group_id": gid,
            "member_removed": member}
