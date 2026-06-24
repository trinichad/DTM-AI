"""Remove a member from a directory (Entra) group (D-65; SOP: m365-graph).
The opposite of m365_add_security_group_member."""
from __future__ import annotations

from typing import Any

NAME = "m365_remove_security_group_member"
DESCRIPTION = ("Remove a user from a DIRECTORY (Entra) group — security or Microsoft 365 group. "
               "Dynamic groups are refused (their membership comes from the rule). For an EMAIL "
               "distribution list use exo_remove_group_member. Remove MANY members from one group "
               "in ONE call by passing `members` (a list) instead of `member` — do NOT call this "
               "tool once per member. Verifies removal before reporting success.")
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
        "members": {"type": "array", "items": {"type": "string"},
                    "description": "remove MANY members from this group in ONE call — a list of "
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
    return {"Remove from group": (f"{name}  ·  {gid}" if name else str(args.get("group") or "")),
            "User": str(args.get("member") or "")}


def run(ctx, group: str, member: str = "", members: Any = None, **_: Any):
    wanted = [m for m in (str(x).strip() for x in (members or [])) if m]
    if wanted:                                         # batch remove (D-110) — ONE call, ONE approval
        results = [_one(ctx, group, m) for m in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "group": group,
                "members_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, group, member)


def _one(ctx, group: str, member: str) -> dict:
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_delete
    from . import _graph_common as g
    try:
        grp, bad = g.resolve_group(ctx, group)
        if bad:
            return bad
        if "DynamicMembership" in [str(t) for t in (grp.get("groupTypes") or [])]:
            return {"ok": False, "member": member, "error":
                    f"'{grp.get('displayName')}' is a DYNAMIC group — membership comes from its "
                    f"rule; members can't be removed manually."}
        # On-prem-mastered group: membership lives in Active Directory and AAD Connect re-syncs it,
        # so a cloud removal either errors or silently reverts ("returned no error but still there").
        # Name the real fix instead of letting the verify fail cryptically (mirrors the D-91 guard).
        if grp.get("onPremisesSyncEnabled") is True:
            return {"ok": False, "member": member, "on_prem_synced": True,
                    "group": grp.get("displayName"),
                    "error": (f"'{grp.get('displayName')}' is synced from on-premises Active "
                              f"Directory (onPremisesSyncEnabled) — its membership is mastered in "
                              f"AD, not Entra, so a cloud removal won't stick. Remove {member} from "
                              f"this group in on-prem AD (Active Directory Users & Computers or the "
                              f"sync source); AAD Connect will then remove it from Entra.")}
        uid, bad = g.resolve_user_id(ctx, member)
        if bad:
            return {**bad, "member": member}
        gid = str(grp.get("id"))
        if not g.is_group_member(ctx, gid, uid):
            return {"ok": True, "group": grp.get("displayName"), "member": member,
                    "note": "not a member — nothing to remove"}
        r = scoped_delete(ctx, "m365", f"/groups/{gid}/members/{uid}/$ref")
        bad = g.fail(r)
        if bad:
            return {**bad, "member": member}
        # Membership reads are eventually-consistent — poll until gone before failing (D-104).
        gone, _ = g.settle(lambda: g.is_group_member(ctx, gid, uid), lambda m: not m)
        still = not gone
    except HttpError as exc:
        return g.err403(exc, "removing the member",
                        "Group.ReadWrite.All (and the signing admin needs a group-management "
                        "role, e.g. Groups Administrator)")
    if still:
        return {"ok": False, "step": "verify", "pending": True,
                "group": grp.get("displayName"), "member": member,
                "error": (f"Entra accepted the removal of {member} from "
                          f"'{grp.get('displayName')}' but still lists them after a short poll — "
                          f"usually replication lag; re-check shortly. If it persists, the group "
                          f"may be on-prem synced or the membership is inherited via a nested group.")}
    return {"ok": True, "group": grp.get("displayName"), "group_id": gid,
            "member_removed": member}
