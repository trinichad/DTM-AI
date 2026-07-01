"""Assign a Google Workspace license (SKU) to a user — Enterprise License Manager API (D-118)."""
from __future__ import annotations

from typing import Any

# Common Google Workspace SKU ids (productId "Google-Apps") — for the model/owner's convenience.
_COMMON_SKUS = {
    "1010020027": "Business Starter",
    "1010020028": "Business Standard",
    "1010020025": "Business Plus",
    "1010020026": "Enterprise Standard",
    "1010020020": "Enterprise Plus",
    "1010020030": "Frontline Starter",
}

NAME = "gws_assign_license"
DESCRIPTION = ("Assign a Google Workspace license (SKU) to a user. Pass the user email and the `sku` "
               "id. Common SKUs (productId 'Google-Apps'): 1010020027 Business Starter, 1010020028 "
               "Business Standard, 1010020025 Business Plus, 1010020026 Enterprise Standard, "
               "1010020020 Enterprise Plus. Override `product` only for a non-default product. To "
               "license MANY users, use the `bulk` tool.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's primary email"},
        "sku": {"type": "string", "description": "the SKU id, e.g. 1010020028 (Business Standard)"},
        "product": {"type": "string",
                    "description": "product id (default 'Google-Apps' — only change if needed)"},
    },
    "required": ["user", "sku"],
    "additionalProperties": False,
}


def run(ctx, user: str = "", sku: str = "", product: str = "Google-Apps", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_write
    from ._gws_write import err_msg, api_error
    u, s = (user or "").strip(), (sku or "").strip()
    p = (product or "Google-Apps").strip()
    if not (u and s):
        return {"ok": False, "error": "both user and sku are required"}
    path = f"/apps/licensing/v1/product/{p}/sku/{s}/user"
    try:
        res = scoped_write(ctx, "gws", path, body={"userId": u}, method="POST")
    except HttpError as e:
        st = getattr(e, "status", None)
        if st == 412:                              # precondition — already assigned
            return {"ok": False, "error": f"'{u}' already has SKU {s}"}
        if st == 404:
            return {"ok": False, "error": f"user '{u}' or SKU '{s}' not found"}
        return {"ok": False, "error": err_msg(e)}
    blocked = api_error(res)
    if blocked:
        return {"ok": False, "error": blocked}
    return {"ok": True, "user": u, "sku": s, "sku_name": _COMMON_SKUS.get(s), "product": p}
