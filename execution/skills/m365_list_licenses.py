"""List the client's Microsoft 365 license SKUs + availability (D-55; SOP: m365-graph)."""
from __future__ import annotations

from typing import Any

NAME = "m365_list_licenses"
DESCRIPTION = ("List the client's Microsoft 365 LICENSES: each SKU with total purchased, "
               "consumed, and AVAILABLE seats. Pass `license` (a SKU name from the list) to "
               "instead show the APPS that license includes — the boxes you can check/uncheck "
               "when assigning. Use before m365_assign_license.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "license": {"type": "string",
                    "description": "show the apps inside ONE license — SKU part number "
                                   "(e.g. O365_BUSINESS_PREMIUM) or SKU GUID (optional)"},
    },
    "additionalProperties": False,
}


def _fetch_skus(ctx):
    """Return (rows, error_dict)."""
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read
    try:
        data = scoped_read(ctx, "m365", "/subscribedSkus")
    except HttpError as e:
        if e.status == 403:
            return None, {"ok": False, "error":
                          "Graph refused (403) — reading licenses needs Organization.Read.All. "
                          "Add it to M365_SCOPES on the M365 card and sign the client in again."}
        return None, {"ok": False, "error": f"Graph HTTP {e.status}: {e.body[:300]}"}
    if isinstance(data, dict) and data.get("error"):
        return None, {"ok": False, "error": str(data["error"])}
    return (data.get("value") if isinstance(data, dict) else data) or [], None


def find_sku(rows: list, want: str):
    """Resolve a SKU by part number or GUID, case-insensitively. Shared with assign (D-55)."""
    w = (want or "").strip().lower()
    return next((s for s in rows if isinstance(s, dict)
                 and w in (str(s.get("skuPartNumber") or "").lower(),
                           str(s.get("skuId") or "").lower())), None)


def run(ctx, license: str = "", **_: Any):
    rows, bad = _fetch_skus(ctx)
    if bad:
        return bad

    if (license or "").strip():                      # apps-inside-one-license view
        sku = find_sku(rows, license)
        if not sku:
            names = [str(s.get("skuPartNumber")) for s in rows if isinstance(s, dict)]
            return {"ok": False, "error": f"this client has no license '{license}' — they own: "
                                          f"{', '.join(names) or '(none)'}"}
        apps = [{"app": p.get("servicePlanName"), "plan_id": p.get("servicePlanId"),
                 "status": p.get("provisioningStatus"), "applies_to": p.get("appliesTo")}
                for p in (sku.get("servicePlans") or []) if isinstance(p, dict)]
        apps.sort(key=lambda a: str(a["app"]))
        return {"license": sku.get("skuPartNumber"), "app_count": len(apps), "apps": apps,
                "note": "apps with applies_to=User can be unchecked per user via "
                        "m365_assign_license disabled_apps; Company-level plans cannot"}

    skus = []
    for s in rows:
        if not isinstance(s, dict):
            continue
        total = int((s.get("prepaidUnits") or {}).get("enabled") or 0)
        used = int(s.get("consumedUnits") or 0)
        skus.append({"license": s.get("skuPartNumber"), "sku_id": s.get("skuId"),
                     "total": total, "used": used, "available": total - used,
                     "status": s.get("capabilityStatus")})
    skus.sort(key=lambda x: -(x["available"]))
    return {"count": len(skus), "licenses": skus,
            "note": "names are Microsoft SKU part numbers (e.g. O365_BUSINESS_PREMIUM = "
                    "Microsoft 365 Business Standard); pass one back as `license` to see its apps"}
