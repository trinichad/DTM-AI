"""List Microsoft 365 user license assignments and consumed SKU details."""
from __future__ import annotations
from typing import Any

NAME = "m365_list_user_license_assignments"
DESCRIPTION = "List Microsoft 365 users with assigned license SKU names and IDs."
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = False
PARAMETERS = {
    "type": "object",
    "properties": {
        "search": {
            "type": "string",
            "description": "Optional case-insensitive substring filter matched against displayName, userPrincipalName, or mail."
        },
        "licensed_only": {
            "type": "boolean",
            "description": "When true, return only users with one or more assigned licenses.",
            "default": True
        },
        "sku": {
            "type": "string",
            "description": "Optional SKU filter by skuPartNumber or skuId."
        },
        "top": {
            "type": "integer",
            "description": "Maximum number of matching user rows to return.",
            "default": 999,
            "minimum": 1,
            "maximum": 10000
        }
    },
    "additionalProperties": False
}


def run(ctx, **kwargs):
    from execution.clients.scopes import scoped_read

    def is_failure(value: Any) -> str | None:
        if isinstance(value, dict):
            err = value.get("error")
            if err:
                return str(err)
            blocked = value.get("blocked")
            if blocked:
                return str(blocked)
            message = value.get("message")
            if isinstance(message, str) and "blocked" in message.lower():
                return message
        return None

    search = kwargs.get("search")
    if search is not None and not isinstance(search, str):
        return {"error": "search must be a string when provided"}
    search_text = search.strip().lower() if isinstance(search, str) and search.strip() else None

    licensed_only = kwargs.get("licensed_only", True)
    if not isinstance(licensed_only, bool):
        return {"error": "licensed_only must be a boolean"}

    sku_filter = kwargs.get("sku")
    if sku_filter is not None and not isinstance(sku_filter, str):
        return {"error": "sku must be a string when provided"}
    sku_filter_text = sku_filter.strip().lower() if isinstance(sku_filter, str) and sku_filter.strip() else None

    top = kwargs.get("top", 999)
    if not isinstance(top, int) or isinstance(top, bool):
        return {"error": "top must be an integer"}
    if top < 1 or top > 10000:
        return {"error": "top must be between 1 and 10000"}

    skus_result = scoped_read(ctx, "m365", "/subscribedSkus")
    failure = is_failure(skus_result)
    if failure:
        return {"error": failure}
    if not isinstance(skus_result, dict):
        return {"error": "unexpected /subscribedSkus response format"}

    sku_items = skus_result.get("value")
    if sku_items is None and "skuId" in skus_result:
        sku_items = [skus_result]
    if not isinstance(sku_items, list):
        return {"error": "unexpected /subscribedSkus response: missing value list"}

    sku_map = {}
    sku_inventory = []
    for item in sku_items:
        if not isinstance(item, dict):
            continue
        sku_id = item.get("skuId")
        sku_part_number = item.get("skuPartNumber")
        if sku_id is None:
            continue
        sku_id_text = str(sku_id)
        sku_name = str(sku_part_number) if sku_part_number is not None else sku_id_text
        prepaid_units = item.get("prepaidUnits")
        sku_map[sku_id_text.lower()] = {
            "sku_id": sku_id_text,
            "sku_part_number": sku_name,
            "consumed_units": item.get("consumedUnits"),
            "prepaid_units": prepaid_units
        }
        sku_inventory.append({
            "sku_id": sku_id_text,
            "sku_part_number": sku_name,
            "consumed_units": item.get("consumedUnits"),
            "prepaid_units": prepaid_units
        })

    if sku_filter_text:
        matched_sku = False
        for mapped in sku_map.values():
            if sku_filter_text == str(mapped.get("sku_id", "")).lower() or sku_filter_text == str(mapped.get("sku_part_number", "")).lower():
                matched_sku = True
                break
        if not matched_sku:
            return {
                "rows": [],
                "summary": {
                    "total_returned_users": 0,
                    "total_licensed_users": 0,
                    "counts_by_sku": {},
                    "sku_filter_matched": False
                },
                "sku_inventory": sku_inventory
            }

    rows = []
    counts_by_sku = {}
    total_licensed_users = 0
    scanned_users = 0
    next_path = "/users?$select=id,displayName,userPrincipalName,mail,accountEnabled,assignedLicenses&$top=999"

    while next_path and len(rows) < top:
        users_result = scoped_read(ctx, "m365", next_path)
        failure = is_failure(users_result)
        if failure:
            return {"error": failure}
        if not isinstance(users_result, dict):
            return {"error": "unexpected /users response format"}

        users = users_result.get("value")
        if users is None and "id" in users_result:
            users = [users_result]
        if not isinstance(users, list):
            return {"error": "unexpected /users response: missing value list"}

        for user in users:
            if len(rows) >= top:
                break
            if not isinstance(user, dict):
                continue

            scanned_users += 1
            display_name = user.get("displayName")
            upn = user.get("userPrincipalName")
            mail = user.get("mail")

            if search_text:
                searchable = " ".join([
                    str(display_name or ""),
                    str(upn or ""),
                    str(mail or "")
                ]).lower()
                if search_text not in searchable:
                    continue

            assigned = user.get("assignedLicenses")
            if assigned is None:
                assigned = []
            if not isinstance(assigned, list):
                assigned = []

            license_sku_ids = []
            license_skus = []
            for assignment in assigned:
                if not isinstance(assignment, dict):
                    continue
                assigned_sku_id = assignment.get("skuId")
                if assigned_sku_id is None:
                    continue
                assigned_sku_id_text = str(assigned_sku_id)
                mapped_sku = sku_map.get(assigned_sku_id_text.lower())
                assigned_sku_name = mapped_sku.get("sku_part_number") if mapped_sku else assigned_sku_id_text
                license_sku_ids.append(assigned_sku_id_text)
                license_skus.append(str(assigned_sku_name))

            has_license = len(license_sku_ids) > 0
            if licensed_only and not has_license:
                continue

            if sku_filter_text:
                user_has_filtered_sku = False
                for idx, sku_id_value in enumerate(license_sku_ids):
                    sku_name_value = license_skus[idx] if idx < len(license_skus) else ""
                    if sku_filter_text == sku_id_value.lower() or sku_filter_text == sku_name_value.lower():
                        user_has_filtered_sku = True
                        break
                if not user_has_filtered_sku:
                    continue

            if has_license:
                total_licensed_users += 1
                for sku_name in license_skus:
                    counts_by_sku[sku_name] = counts_by_sku.get(sku_name, 0) + 1

            rows.append({
                "display_name": display_name,
                "user_principal_name": upn,
                "email": mail,
                "enabled": user.get("accountEnabled"),
                "license_skus": license_skus,
                "license_sku_ids": license_sku_ids
            })

        next_link = users_result.get("@odata.nextLink")
        if not next_link:
            next_path = None
        elif isinstance(next_link, str):
            if next_link.startswith("https://graph.microsoft.com/v1.0"):
                next_path = next_link[len("https://graph.microsoft.com/v1.0"):]
            elif next_link.startswith("https://graph.microsoft.com/beta"):
                return {"error": "received beta nextLink for v1.0 users query"}
            elif next_link.startswith("/"):
                next_path = next_link
            else:
                marker = "/users?"
                marker_index = next_link.find(marker)
                if marker_index >= 0:
                    next_path = next_link[marker_index:]
                else:
                    return {"error": "unable to follow @odata.nextLink returned by Microsoft Graph"}
        else:
            return {"error": "unexpected @odata.nextLink format returned by Microsoft Graph"}

    return {
        "rows": rows,
        "summary": {
            "total_returned_users": len(rows),
            "total_licensed_users": total_licensed_users,
            "counts_by_sku": counts_by_sku,
            "scanned_users": scanned_users,
            "truncated": len(rows) >= top and next_path is not None
        },
        "sku_inventory": sku_inventory
    }