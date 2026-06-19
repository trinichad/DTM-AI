"""Remove a license from a user (D-65; SOP: m365-graph). The opposite of m365_assign_license."""
from __future__ import annotations

from typing import Any

NAME = "m365_remove_license"
DESCRIPTION = ("Remove a Microsoft 365 LICENSE from a user (frees the seat). Pass the user and "
               "the license name as shown by m365_list_licenses. WARNING: removing a license "
               "removes its services for the user — a mailbox over 50 GB needs a license to "
               "keep its data. Verifies the license is gone before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address"},
        "license": {"type": "string",
                    "description": "SKU part number (e.g. O365_BUSINESS_PREMIUM) or GUID"},
    },
    "required": ["user", "license"],
    "additionalProperties": False,
}


def run(ctx, user: str, license: str, **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read, scoped_write
    from . import _graph_common as g
    from .m365_list_licenses import find_sku
    user, want = (user or "").strip(), (license or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a sign-in address"}
    try:
        skus = scoped_read(ctx, "m365", "/subscribedSkus")
        bad = g.fail(skus)
        if bad:
            return bad
        sku = find_sku(g.rows(skus), want)
        if not sku:
            names = [str(s.get("skuPartNumber")) for s in g.rows(skus)]
            return {"ok": False, "error": f"this client has no license '{want}' — owns: "
                                          f"{', '.join(names) or '(none)'}"}
        sku_id = str(sku.get("skuId"))
        u = scoped_read(ctx, "m365", f"/users/{user}",
                        {"$select": "id,assignedLicenses"})
        bad = g.fail(u)
        if bad:
            return bad
        if not (isinstance(u, dict) and u.get("id")):
            return {"ok": False, "error": f"no user '{user}' found"}
        if not any(str(l.get("skuId")) == sku_id for l in (u.get("assignedLicenses") or [])
                   if isinstance(l, dict)):
            return {"ok": True, "user": user, "license": sku.get("skuPartNumber"),
                    "note": "the user doesn't have that license — nothing to remove"}
        r = scoped_write(ctx, "m365", f"/users/{user}/assignLicense",
                         body={"addLicenses": [], "removeLicenses": [sku_id]}, method="POST")
        bad = g.fail(r)
        if bad:
            return bad
        check = scoped_read(ctx, "m365", f"/users/{user}", {"$select": "assignedLicenses"})
    except HttpError as exc:
        return g.err403(exc, "removing the license", "LicenseAssignment.ReadWrite.All")
    still = any(str(l.get("skuId")) == sku_id
                for l in ((check or {}).get("assignedLicenses") or []) if isinstance(l, dict))
    if still:
        return {"ok": False, "step": "verify",
                "error": "the license is still on the user after removal — check the admin center"}
    return {"ok": True, "user": user, "license_removed": sku.get("skuPartNumber")}
