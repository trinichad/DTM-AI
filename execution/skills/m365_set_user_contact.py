"""Set a user's contact / directory profile fields (D-106; SOP: m365-graph).
Onboarding helper — the standalone update of the contact attributes m365_create_user sets on create."""
from __future__ import annotations

from typing import Any, Optional

NAME = "m365_set_user_contact"
DESCRIPTION = ("Set a user's CONTACT / profile fields: job title, department, office, office phone, "
               "mobile phone, street address, city, state/province, zip/postal code. Pass only the "
               "ones you want to change. In a HYBRID (directory-synced) tenant these attributes are "
               "mastered in on-prem Active Directory, so it refuses with guidance to set them in AD "
               "instead. Pass `users` (a list) to set the SAME fields on MANY people in ONE "
               "call — do NOT call this tool once per user. Verifies the change before "
               "reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
# friendly param → Graph user property (string-valued). office_phone is handled separately
# (businessPhones is an array). Mirrors m365_create_user's mapping.
_FIELDS = {
    "job_title": "jobTitle",
    "department": "department",
    "office": "officeLocation",
    "mobile_phone": "mobilePhone",
    "street_address": "streetAddress",
    "city": "city",
    "state": "state",
    "postal_code": "postalCode",
}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address (UPN)"},
        "users": {"type": "array", "items": {"type": "string"},
                  "description": "act on MANY users in ONE call — a list of sign-in addresses "
                                 "(UPNs); results come back together. Use this instead of "
                                 "calling the tool once per user."},
        "job_title": {"type": "string", "description": "job title"},
        "department": {"type": "string", "description": "department"},
        "office": {"type": "string", "description": "office location"},
        "office_phone": {"type": "string", "description": "office / business phone"},
        "mobile_phone": {"type": "string", "description": "mobile phone"},
        "street_address": {"type": "string", "description": "street address"},
        "city": {"type": "string", "description": "city"},
        "state": {"type": "string", "description": "state or province"},
        "postal_code": {"type": "string", "description": "zip / postal code"},
    },
    "required": ["user"],
    "additionalProperties": False,
}


def _norm(v: Any) -> str:
    return str(v if v is not None else "").strip()


def run(ctx, user: str = "", users: Any = None, office_phone: Optional[str] = None,
        **fields: Any):
    wanted = [str(u).strip() for u in (users or []) if str(u).strip()]
    if wanted:
        results = ctx.map_progress(wanted[:500], lambda u: _one(ctx, u, office_phone, **fields))
        return {"ok": any(r.get("ok") for r in results), "users_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, user, office_phone, **fields)


def _one(ctx, user: str, office_phone: Optional[str] = None, **fields: Any) -> dict:
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read, scoped_write
    from . import _graph_common as g
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "user": user, "error": f"'{user}' is not a sign-in address"}

    # build the PATCH body from whatever fields were actually supplied
    body: dict[str, Any] = {}
    for param, prop in _FIELDS.items():
        val = _norm(fields.get(param))
        if val:
            body[prop] = val
    if _norm(office_phone):
        body["businessPhones"] = [_norm(office_phone)]
    if not body:
        return {"ok": False, "user": user,
                "error": "no contact fields provided — pass at least one to set"}

    try:
        u0 = scoped_read(ctx, "m365", f"/users/{user}",
                         {"$select": "id,onPremisesSyncEnabled"})
        bad = g.fail(u0)
        if bad:
            return {**bad, "user": user}
        uid = str((u0 or {}).get("id") or "")
        if not uid:
            return {"ok": False, "user": user,
                    "error": f"no user '{user}' found in this client"}
        if u0.get("onPremisesSyncEnabled") is True:
            return {"ok": False, "on_prem_synced": True, "user": user,
                    "error": (f"{user} is directory-synced (hybrid) — contact attributes are "
                              f"mastered in on-prem Active Directory and can't be set in the cloud. "
                              f"Set them in AD (they sync to Entra).")}
        r = scoped_write(ctx, "m365", f"/users/{uid}", body=body, method="PATCH")
        bad = g.fail(r)
        if bad:
            return {**bad, "user": user}
        # re-read (eventually-consistent) and confirm every field landed (D-104)
        want = {k: (v[0] if isinstance(v, list) else v) for k, v in body.items()}
        sel = "id," + ",".join(body)
        ok, check = g.settle(
            lambda: scoped_read(ctx, "m365", f"/users/{uid}", {"$select": sel}),
            lambda c: isinstance(c, dict) and all(
                (str((c.get(k) or [""])[0]) if k == "businessPhones" else str(c.get(k) or ""))
                == str(w) for k, w in want.items()))
    except HttpError as exc:
        return {**g.err403(exc, "updating the contact info", "User.ReadWrite.All"), "user": user}
    if not ok:
        return {"ok": False, "step": "verify", "pending": True, "user": user,
                "error": "the update returned but the new values aren't showing yet — usually "
                         "propagation lag; re-check shortly"}
    return {"ok": True, "user": user, "updated": sorted(body)}
