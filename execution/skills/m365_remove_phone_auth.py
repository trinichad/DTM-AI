"""Remove a user's phone authentication method (D-65; SOP: m365-graph).
The opposite of m365_add_phone_auth."""
from __future__ import annotations

from typing import Any

NAME = "m365_remove_phone_auth"
DESCRIPTION = ("Remove a user's PHONE authentication (MFA) method. phone_type mobile (default), "
               "alternateMobile, or office. WARNING: if it's the user's only MFA method they "
               "may be locked out or forced to re-register. Verifies removal before reporting "
               "success.")
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
        "phone_type": {"type": "string", "enum": list(_TYPES),
                       "description": "which phone slot (default mobile)"},
    },
    "required": ["user"],
    "additionalProperties": False,
}


def run(ctx, user: str, phone_type: str = "mobile", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_delete, scoped_read
    from . import _graph_common as g
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a sign-in address"}
    ptype = next((t for t in _TYPES if t.lower() == (phone_type or "").strip().lower()), None)
    if not ptype:
        return {"ok": False, "error": f"phone_type must be one of: {', '.join(_TYPES)}"}
    base = f"/users/{user}/authentication/phoneMethods"
    try:
        cur = scoped_read(ctx, "m365", base)
        bad = g.fail(cur)
        if bad:
            return bad
        method = next((m for m in g.rows(cur) if str(m.get("phoneType")) == ptype), None)
        if not method:
            return {"ok": True, "user": user, "phone_type": ptype,
                    "note": f"the user has no {ptype} phone method — nothing to remove"}
        r = scoped_delete(ctx, "m365", f"{base}/{method.get('id')}")
        bad = g.fail(r)
        if bad:
            return bad
        check = scoped_read(ctx, "m365", base)
    except HttpError as exc:
        return g.err403(exc, "removing the phone method",
                        "UserAuthenticationMethod.ReadWrite.All")
    if any(str(m.get("phoneType")) == ptype for m in g.rows(check)):
        return {"ok": False, "step": "verify",
                "error": f"the {ptype} phone method is still present after removal — check Entra"}
    return {"ok": True, "user": user, "phone_removed": ptype}
