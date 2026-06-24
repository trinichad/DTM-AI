"""Remove a user's phone authentication method (D-65; SOP: m365-graph).
The opposite of m365_add_phone_auth."""
from __future__ import annotations

from typing import Any

NAME = "m365_remove_phone_auth"
DESCRIPTION = ("Remove a user's PHONE authentication (MFA) method. phone_type mobile (default), "
               "alternateMobile, or office. WARNING: if it's the user's only MFA method they "
               "may be locked out or forced to re-register. Pass `users` (a list) to remove the "
               "method from MANY people in ONE call — do NOT call this tool once per user. "
               "Verifies removal before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_TYPES = ("mobile", "alternateMobile", "office")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address"},
        "users": {"type": "array", "items": {"type": "string"},
                  "description": "act on MANY users in ONE call — a list of sign-in addresses "
                                 "(UPNs); results come back together. Use this instead of "
                                 "calling the tool once per user."},
        "phone_type": {"type": "string", "enum": list(_TYPES),
                       "description": "which phone slot (default mobile)"},
    },
    "additionalProperties": False,
}


def run(ctx, user: str = "", users: Any = None, phone_type: str = "mobile", **_: Any):
    wanted = [str(u).strip() for u in (users or []) if str(u).strip()]
    if wanted:
        results = [_one(ctx, u, phone_type) for u in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "users_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, user, phone_type)


def _one(ctx, user: str, phone_type: str = "mobile") -> dict:
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_delete, scoped_read
    from . import _graph_common as g
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "user": user, "error": f"'{user}' is not a sign-in address"}
    ptype = next((t for t in _TYPES if t.lower() == (phone_type or "").strip().lower()), None)
    if not ptype:
        return {"ok": False, "user": user,
                "error": f"phone_type must be one of: {', '.join(_TYPES)}"}
    base = f"/users/{user}/authentication/phoneMethods"
    try:
        cur = scoped_read(ctx, "m365", base)
        bad = g.fail(cur)
        if bad:
            return {**bad, "user": user}
        method = next((m for m in g.rows(cur) if str(m.get("phoneType")) == ptype), None)
        if not method:
            return {"ok": True, "user": user, "phone_type": ptype,
                    "note": f"the user has no {ptype} phone method — nothing to remove"}
        r = scoped_delete(ctx, "m365", f"{base}/{method.get('id')}")
        bad = g.fail(r)
        if bad:
            return {**bad, "user": user}
        # Auth-method reads are eventually-consistent — poll until the method is gone (D-104).
        _ok, check = g.settle(
            lambda: scoped_read(ctx, "m365", base),
            lambda c: not any(str(m.get("phoneType")) == ptype for m in g.rows(c)))
    except HttpError as exc:
        return {**g.err403(exc, "removing the phone method",
                           "UserAuthenticationMethod.ReadWrite.All"), "user": user}
    if any(str(m.get("phoneType")) == ptype for m in g.rows(check)):
        return {"ok": False, "user": user, "step": "verify", "pending": True,
                "error": f"the {ptype} phone method still shows after removal — usually propagation "
                         f"lag; re-check in Entra shortly"}
    return {"ok": True, "user": user, "phone_removed": ptype}
