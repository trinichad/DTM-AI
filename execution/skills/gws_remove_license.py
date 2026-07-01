"""Remove a Google Workspace license (SKU) from a user — Enterprise License Manager API (D-118)."""
from __future__ import annotations

from typing import Any

NAME = "gws_remove_license"
DESCRIPTION = ("Remove a Google Workspace license (SKU) from a user — frees the seat. Pass the user "
               "email and the `sku` id (see gws_assign_license for common SKUs). Override `product` "
               "only for a non-default product. Common in offboarding.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's primary email"},
        "sku": {"type": "string", "description": "the SKU id to remove"},
        "product": {"type": "string",
                    "description": "product id (default 'Google-Apps' — only change if needed)"},
    },
    "required": ["user", "sku"],
    "additionalProperties": False,
}


def run(ctx, user: str = "", sku: str = "", product: str = "Google-Apps", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_delete
    from ._gws_write import err_msg, api_error
    u, s = (user or "").strip(), (sku or "").strip()
    p = (product or "Google-Apps").strip()
    if not (u and s):
        return {"ok": False, "error": "both user and sku are required"}
    try:
        res = scoped_delete(ctx, "gws", f"/apps/licensing/v1/product/{p}/sku/{s}/user/{u}")
    except HttpError as e:
        if getattr(e, "status", None) == 404:
            return {"ok": False, "error": f"'{u}' does not have SKU {s} (or it doesn't exist)"}
        return {"ok": False, "error": err_msg(e)}
    blocked = api_error(res)
    if blocked:
        return {"ok": False, "error": blocked}
    return {"ok": True, "user": u, "removed_sku": s, "product": p}
