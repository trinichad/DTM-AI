"""Every distribution / mail-enabled group a user belongs to — Exchange-authoritative
(D-105; SOP: exchange-online). The EXO counterpart to m365_user_groups (which is Graph and can
miss classic distribution lists)."""
from __future__ import annotations

from typing import Any

NAME = "exo_user_distribution_groups"
DESCRIPTION = ("List every EMAIL group a user is a member of — distribution lists, mail-enabled "
               "security groups, and Microsoft 365 groups — straight from Exchange, which is the "
               "authoritative source for distribution lists (Graph / m365_user_groups can miss "
               "classic DLs). Use it before offboarding to see which mail groups to remove a "
               "terminated user from. Direct memberships only. Pair with exo_remove_group_member "
               "to remove. Pass `users` (a list) to check MANY in ONE call — do NOT call this "
               "tool once per user.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address"},
        "users": {"type": "array", "items": {"type": "string"},
                  "description": "act on MANY in ONE call — a list of mailbox addresses; results "
                                 "come back together. Use this instead of calling the tool once "
                                 "per mailbox."},
    },
    "required": [],
    "additionalProperties": False,
}

# RecipientTypeDetails → (kind, removable). Dynamic DLs are rule-based — not manually removable.
_KINDS = {
    "MailUniversalDistributionGroup": ("distribution", True),
    "MailUniversalSecurityGroup": ("mail-enabled security", True),
    "GroupMailbox": ("microsoft365", True),
    "DynamicDistributionGroup": ("dynamic distribution", False),
    "RoomList": ("room list", True),
}


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def memberships(ctx, user: str) -> dict[str, Any]:
    """The core read, reusable by m365_offboard_user. Returns {ok, groups|error}."""
    from . import _exo_common as c
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a sign-in address"}
    exo = ctx.client("exo")
    mb, bad = c.get_one_mailbox(exo, user)
    if bad:
        return {"ok": False, "error": bad.get("error")}
    dn = str(mb.get("DistinguishedName") or "")
    if not dn:
        return {"ok": False, "error": f"no DistinguishedName for '{user}' — can't query group "
                                      f"membership"}
    # OPATH filter: groups whose Members include this user's DN (only groups have Members, so the
    # result is groups only). Double any apostrophe in the DN so the filter stays well-formed.
    r = exo.invoke("Get-Recipient",
                   {"Filter": f"Members -eq '{dn.replace(chr(39), chr(39) * 2)}'",
                    "ResultSize": 1000})
    if c.err(r):
        return {"ok": False, "error": c.err(r)}
    groups: list[dict] = []
    for row in _rows(r):
        rtd = str(row.get("RecipientTypeDetails") or "")
        kind, removable = _KINDS.get(rtd, ("other", True))
        groups.append({"name": row.get("DisplayName"),
                       "email": row.get("PrimarySmtpAddress"), "kind": kind,
                       "removable": removable,
                       "remove_with": "exo_remove_group_member" if removable else None})
    groups.sort(key=lambda x: (str(x.get("kind")), str(x.get("name") or "").lower()))
    return {"ok": True, "groups": groups}


def run(ctx, user: str = "", users: Any = None, **_: Any):
    wanted = [str(x).strip() for x in (users or []) if str(x).strip()]
    if wanted:                                          # batch (D-110) — one call, many users
        results = ctx.map_progress(wanted[:500], lambda x: _one(ctx, x))
        return {"ok": any(r.get("ok") for r in results), "users_checked": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, user)


def _one(ctx, user: str) -> dict:
    res = memberships(ctx, user)
    if not res.get("ok"):
        return {**res, "user": user}
    groups = res["groups"]
    out: dict[str, Any] = {"ok": True, "user": (user or "").strip(),
                           "count": len(groups), "groups": groups}
    dyn = [g["name"] for g in groups if not g["removable"]]
    if dyn:
        out["note"] = (f"{len(dyn)} dynamic group(s) are rule-based — membership can't be removed "
                       f"manually; change the rule/attribute instead.")
    return out
