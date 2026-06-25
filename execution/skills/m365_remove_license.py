"""Remove a license from a user (D-65; SOP: m365-graph). The opposite of m365_assign_license."""
from __future__ import annotations

from typing import Any

NAME = "m365_remove_license"
DESCRIPTION = ("Remove a Microsoft 365 LICENSE from a user (frees the seat). Pass the user and "
               "the license name as shown by m365_list_licenses. WARNING: removing a license "
               "removes its services for the user — a mailbox over 50 GB needs a license to "
               "keep its data. Pass `users` (a list) to remove the license from MANY people in "
               "ONE call — do NOT call this tool once per user. Verifies the license is gone "
               "before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address"},
        "users": {"type": "array", "items": {"type": "string"},
                  "description": "act on MANY users in ONE call — a list of sign-in addresses "
                                 "(UPNs); results come back together. Use this instead of "
                                 "calling the tool once per user."},
        "license": {"type": "string",
                    "description": "SKU part number (e.g. O365_BUSINESS_PREMIUM) or GUID"},
    },
    "required": ["license"],
    "additionalProperties": False,
}


def run(ctx, user: str = "", users: Any = None, license: str = "", **_: Any):
    wanted = [str(u).strip() for u in (users or []) if str(u).strip()]
    if wanted:
        results = ctx.map_progress(wanted[:500], lambda u: _one(ctx, u, license))
        return {"ok": any(r.get("ok") for r in results), "users_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, user, license)


def _one(ctx, user: str, license: str) -> dict:
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read, scoped_write
    from . import _graph_common as g
    from .m365_list_licenses import find_sku
    user, want = (user or "").strip(), (license or "").strip()
    if "@" not in user:
        return {"ok": False, "user": user, "error": f"'{user}' is not a sign-in address"}
    try:
        skus = scoped_read(ctx, "m365", "/subscribedSkus")
        bad = g.fail(skus)
        if bad:
            return {**bad, "user": user}
        sku = find_sku(g.rows(skus), want)
        if not sku:
            names = [str(s.get("skuPartNumber")) for s in g.rows(skus)]
            return {"ok": False, "user": user,
                    "error": f"this client has no license '{want}' — owns: "
                             f"{', '.join(names) or '(none)'}"}
        sku_id = str(sku.get("skuId"))
        u = scoped_read(ctx, "m365", f"/users/{user}",
                        {"$select": "id,assignedLicenses"})
        bad = g.fail(u)
        if bad:
            return {**bad, "user": user}
        if not (isinstance(u, dict) and u.get("id")):
            return {"ok": False, "user": user, "error": f"no user '{user}' found"}
        if not any(str(l.get("skuId")) == sku_id for l in (u.get("assignedLicenses") or [])
                   if isinstance(l, dict)):
            return {"ok": True, "user": user, "license": sku.get("skuPartNumber"),
                    "note": "the user doesn't have that license — nothing to remove"}
        r = scoped_write(ctx, "m365", f"/users/{user}/assignLicense",
                         body={"addLicenses": [], "removeLicenses": [sku_id]}, method="POST")
        bad = g.fail(r)
        if bad:
            return {**bad, "user": user}
        # assignedLicenses lags the POST by a few seconds — poll until gone before failing (D-104).
        gone, _ = g.settle(
            lambda: scoped_read(ctx, "m365", f"/users/{user}", {"$select": "assignedLicenses"}),
            lambda c: not g.fail(c) and not any(
                str(l.get("skuId")) == sku_id
                for l in ((c or {}).get("assignedLicenses") or []) if isinstance(l, dict)))
        still = not gone
    except HttpError as exc:
        return {**g.err403(exc, "removing the license",
                           "LicenseAssignment.ReadWrite.All"), "user": user}
    if still:
        return {"ok": False, "step": "verify", "pending": True, "user": user,
                "license": sku.get("skuPartNumber"),
                "error": (f"Microsoft 365 accepted the removal of {sku.get('skuPartNumber')} from "
                          f"{user} but still lists it after a short poll — license changes can take "
                          f"a moment to propagate; re-check with m365_list_user_license_assignments "
                          f"shortly rather than re-running.")}
    return {"ok": True, "user": user, "license_removed": sku.get("skuPartNumber")}
