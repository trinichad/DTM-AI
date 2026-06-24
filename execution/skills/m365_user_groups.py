"""Every directory (Entra) group a user belongs to (D-102; SOP: m365-graph)."""
from __future__ import annotations

from typing import Any

NAME = "m365_user_groups"
DESCRIPTION = ("List every DIRECTORY (Entra) group a user is a member of — security groups, "
               "Microsoft 365 groups, distribution / mail-enabled groups, and dynamic groups. "
               "Use it before offboarding to see WHICH groups to remove a terminated user from. "
               "Pass `user` for one person or `users` (a list) to check MANY in ONE call — do NOT "
               "call this tool once per person. Returns DIRECT memberships by default (the ones "
               "you can actually remove); set transitive=true to also see groups inherited through "
               "nested groups. Each group is tagged with its kind and whether it's manually "
               "removable (dynamic groups are rule-based and can't be). Pair with "
               "m365_remove_security_group_member (security / M365 groups) or "
               "exo_remove_group_member (distribution lists) to remove.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address (UPN)"},
        "users": {"type": "array", "items": {"type": "string"},
                  "description": "check MANY users in ONE call — a list of sign-in addresses; "
                                 "each user's group memberships come back together. Use this "
                                 "instead of calling the tool once per user."},
        "transitive": {"type": "boolean",
                       "description": "also include groups inherited via nested groups "
                                      "(default false = direct memberships only)"},
    },
    "additionalProperties": False,
}

_SELECT = ("id,displayName,mail,mailNickname,securityEnabled,mailEnabled,groupTypes,"
           "membershipRule")
_MAX_PAGES = 20            # ~20k groups before we stop paging — far beyond any real user


def run(ctx, user: str = "", users: Any = None, transitive: bool = False, **_: Any):
    wanted = [str(u).strip() for u in (users or []) if str(u).strip()]
    if wanted:                                         # batch lookup (D-110) — one call, many users
        results = [_one(ctx, u, transitive) for u in wanted]
        return {"ok": True, "users_checked": len(results),
                "scope": "transitiveMemberOf" if transitive else "memberOf",
                "results": results}
    return _one(ctx, user, transitive)


def _one(ctx, user: str, transitive: bool = False) -> dict:
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read
    from . import _graph_common as g
    from .m365_list_entra_groups import classify

    uid, bad = g.resolve_user_id(ctx, user)
    if bad:
        return {**bad, "user": user}
    # Typed cast → only group objects come back (memberOf otherwise also returns directoryRole
    # entries, which aren't groups and aren't removable).
    rel = "transitiveMemberOf" if transitive else "memberOf"
    path = f"/users/{uid}/{rel}/microsoft.graph.group"
    params: dict[str, Any] = {"$select": _SELECT, "$top": 999}

    groups: list[dict] = []
    for _ in range(_MAX_PAGES):
        try:
            data = scoped_read(ctx, "m365", path, params)
        except HttpError as exc:
            return g.err403(exc, "listing a user's group memberships", "GroupMember.Read.All")
        bad = g.fail(data)
        if bad:
            return bad
        for grp in g.rows(data):
            kind = classify(grp)
            dynamic = kind == "dynamic"
            groups.append({
                "name": grp.get("displayName"), "id": grp.get("id"),
                "email": grp.get("mail"), "kind": kind,
                # how the agent removes this membership (offboarding) — dynamic = not manual
                "removable": not dynamic,
                "remove_with": (None if dynamic
                                else "exo_remove_group_member"
                                if kind == "distribution/mail-enabled"
                                else "m365_remove_security_group_member"),
            })
        nxt = data.get("@odata.nextLink") if isinstance(data, dict) else None
        if not nxt:
            break
        params = {"$skiptoken": g._skiptoken(nxt)}

    groups.sort(key=lambda r: (str(r.get("kind")), str(r.get("name") or "").lower()))
    out: dict[str, Any] = {"ok": True, "user": user, "scope": rel,
                           "count": len(groups), "groups": groups}
    dyn = [r["name"] for r in groups if not r["removable"]]
    if dyn:
        out["note"] = (f"{len(dyn)} dynamic group(s) ({', '.join(str(d) for d in dyn[:5])}"
                       f"{'…' if len(dyn) > 5 else ''}) are RULE-BASED — membership can't be "
                       f"removed manually; exclude the user via the membership rule or attribute "
                       f"instead.")
    return out
