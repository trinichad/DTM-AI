"""Assign a Microsoft 365 license to a user — with per-app check/uncheck (D-55; SOP: m365-graph)."""
from __future__ import annotations

from typing import Any, Optional

NAME = "m365_assign_license"
DESCRIPTION = ("Assign a Microsoft 365 LICENSE to a user, optionally UNCHECKING some of its "
               "apps. Pass the user's sign-in address and the license name exactly as shown by "
               "m365_list_licenses. `disabled_apps` = the complete list of that license's apps "
               "to leave UNchecked (names from m365_list_licenses with license=...; empty list "
               "= all apps on). If the user ALREADY has the license, pass disabled_apps to "
               "change which apps are checked. Refuses if no seats are available. Sets the "
               "user's usage location automatically when missing. Verifies before reporting "
               "success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address (UPN)"},
        "license": {"type": "string",
                    "description": "SKU part number from m365_list_licenses "
                                   "(e.g. O365_BUSINESS_PREMIUM) or a SKU GUID"},
        "disabled_apps": {"type": "array", "items": {"type": "string"},
                          "description": "COMPLETE list of the license's apps to leave "
                                         "UNCHECKED (service-plan names or GUIDs from "
                                         "m365_list_licenses license=...); [] = enable all; "
                                         "omit to leave existing choices alone"},
        "usage_location": {"type": "string",
                           "description": "2-letter country code set on the user if they have "
                                          "none (default US — required by Microsoft to license)"},
    },
    "required": ["user", "license"],
    "additionalProperties": False,
}


def _graph_err(e, doing: str) -> dict:
    if e.status == 403:
        return {"ok": False, "error":
                f"Graph refused (403) while {doing} — needs User.ReadWrite.All (+ "
                f"Organization.Read.All) in M365_SCOPES; re-sign-in the client after adding."}
    return {"ok": False, "error": f"Graph HTTP {e.status} while {doing}: {e.body[:300]}"}


def _user_plans(sku: dict) -> list[dict]:
    """The plans an admin can check/uncheck — only appliesTo=User is disableable per user."""
    return [p for p in (sku.get("servicePlans") or [])
            if isinstance(p, dict) and str(p.get("appliesTo")) == "User"]


def _resolve_apps(sku: dict, wanted: list) -> tuple[Optional[list[str]], Optional[dict]]:
    """Map app names/GUIDs → servicePlanId list. Returns (plan_ids, None) or (None, error)."""
    plans = _user_plans(sku)
    by_key = {}
    for p in plans:
        by_key[str(p.get("servicePlanName") or "").lower()] = str(p.get("servicePlanId"))
        by_key[str(p.get("servicePlanId") or "").lower()] = str(p.get("servicePlanId"))
    ids, unknown = [], []
    for w in wanted:
        hit = by_key.get(str(w or "").strip().lower())
        (ids.append(hit) if hit else unknown.append(str(w)))
    if unknown:
        names = sorted(str(p.get("servicePlanName")) for p in plans)
        return None, {"ok": False, "error":
                      f"unknown app(s) for {sku.get('skuPartNumber')}: {', '.join(unknown)} — "
                      f"uncheckable apps are: {', '.join(names) or '(none)'}"}
    return sorted(set(ids)), None


def _names_for(sku: dict, plan_ids: set) -> list[str]:
    return sorted(str(p.get("servicePlanName")) for p in (sku.get("servicePlans") or [])
                  if isinstance(p, dict) and str(p.get("servicePlanId")) in plan_ids)


def run(ctx, user: str, license: str, disabled_apps: Optional[list] = None,
        usage_location: str = "US", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read, scoped_write
    from .m365_list_licenses import find_sku
    user = (user or "").strip()
    want = (license or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a sign-in address"}

    try:
        skus = scoped_read(ctx, "m365", "/subscribedSkus")
    except HttpError as e:
        return _graph_err(e, "listing licenses")
    rows = (skus.get("value") if isinstance(skus, dict) else skus) or []
    sku = find_sku(rows, want)
    if not sku:
        names = [str(s.get("skuPartNumber")) for s in rows if isinstance(s, dict)]
        return {"ok": False, "error": f"this client has no license '{want}' — they own: "
                                      f"{', '.join(names) or '(none)'}"}
    sku_id = str(sku.get("skuId"))

    disable_ids: Optional[list[str]] = None
    if disabled_apps is not None:
        disable_ids, bad = _resolve_apps(sku, list(disabled_apps))
        if bad:
            return bad

    try:
        u = scoped_read(ctx, "m365", f"/users/{user}",
                        {"$select": "id,usageLocation,assignedLicenses"})
        if isinstance(u, dict) and u.get("error"):
            return {"ok": False, "error": str(u["error"])}
        if not (isinstance(u, dict) and u.get("id")):
            return {"ok": False, "error": f"no user '{user}' found in this client"}
        held = next((l for l in (u.get("assignedLicenses") or [])
                     if isinstance(l, dict) and str(l.get("skuId")) == sku_id), None)
        if held and disable_ids is None:
            return {"ok": True, "user": user, "license": sku.get("skuPartNumber"),
                    "note": "the user already has this license — pass disabled_apps to change "
                            "which of its apps are checked"}
        if held and disable_ids is not None:
            current = sorted(str(p) for p in (held.get("disabledPlans") or []))
            if current == disable_ids:
                return {"ok": True, "user": user, "license": sku.get("skuPartNumber"),
                        "disabled_apps": _names_for(sku, set(disable_ids)),
                        "note": "the license already has exactly those apps unchecked — "
                                "nothing to do"}
        if not held:
            # a FRESH assign consumes a seat; editing the apps on a held license does not
            total = int((sku.get("prepaidUnits") or {}).get("enabled") or 0)
            available = total - int(sku.get("consumedUnits") or 0)
            if available <= 0:
                return {"ok": False, "error": f"{sku.get('skuPartNumber')} has no seats "
                                              f"available ({total} owned, all in use) — buy "
                                              f"more or free one first"}
        location_set = None
        if not (u.get("usageLocation") or "").strip():
            loc = (usage_location or "US").strip().upper()[:2]
            scoped_write(ctx, "m365", f"/users/{user}",
                         body={"usageLocation": loc}, method="PATCH")
            location_set = loc
        send_disabled = disable_ids if disable_ids is not None else []
        r = scoped_write(ctx, "m365", f"/users/{user}/assignLicense",
                         body={"addLicenses": [{"skuId": sku_id,
                                                "disabledPlans": send_disabled}],
                               "removeLicenses": []}, method="POST")
        if isinstance(r, dict) and r.get("error"):
            return {"ok": False, "step": "assign", "error": str(r["error"])}
        check = scoped_read(ctx, "m365", f"/users/{user}", {"$select": "assignedLicenses"})
    except HttpError as e:
        return _graph_err(e, "assigning the license")

    after = next((l for l in ((check or {}).get("assignedLicenses") or [])
                  if isinstance(l, dict) and str(l.get("skuId")) == sku_id), None)
    if not after:
        return {"ok": False, "step": "verify",
                "error": "the assign call returned but the license is not on the user yet — "
                         "check the M365 admin center before retrying"}
    got_disabled = sorted(str(p) for p in (after.get("disabledPlans") or []))
    if got_disabled != sorted(send_disabled):
        return {"ok": False, "step": "verify",
                "error": "the license is assigned but the app choices did not match what was "
                         "requested — check the M365 admin center",
                "requested_unchecked": _names_for(sku, set(send_disabled)),
                "actual_unchecked": _names_for(sku, set(got_disabled))}

    disabled_set = set(got_disabled)
    all_user_plans = {str(p.get("servicePlanId")) for p in _user_plans(sku)}
    out: dict[str, Any] = {
        "ok": True, "user": user,
        ("license_updated" if held else "license_assigned"): sku.get("skuPartNumber"),
        "apps_enabled": _names_for(sku, all_user_plans - disabled_set),
        "apps_disabled": _names_for(sku, disabled_set)}
    if not held:
        total = int((sku.get("prepaidUnits") or {}).get("enabled") or 0)
        out["seats_left_after"] = total - int(sku.get("consumedUnits") or 0) - 1
    if location_set:
        out["usage_location_set"] = location_set
    return out
