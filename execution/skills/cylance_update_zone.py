"""Update a Cylance zone — name / policy / criticality (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_update_zone"
DESCRIPTION = ("Update a Cylance zone by `zone_id`: change its `name`, `policy_id`, and/or "
               "`criticality` (Low/Normal/High). Only the fields you pass change.")
SOURCE = "cylance"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_CRIT = ("Low", "Normal", "High")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "zone_id": {"type": "string", "description": "the Cylance zone id (GUID)"},
        "name": {"type": "string", "description": "new zone name (optional)"},
        "policy_id": {"type": "string", "description": "new policy id (optional)"},
        "criticality": {"type": "string", "enum": list(_CRIT), "description": "Low/Normal/High (optional)"},
    },
    "required": ["zone_id"],
    "additionalProperties": False,
}


def run(ctx, zone_id: str, name: str = "", policy_id: str = "", criticality: str = "", **_: Any):
    zid = (zone_id or "").strip()
    if not re.match(r"^[A-Za-z0-9-]+$", zid):
        return {"ok": False, "error": "zone_id is not valid"}
    client = ctx.client("cylance")
    cur = client.get(f"/zones/v2/{zid}")            # PUT replaces — start from the current record
    if not isinstance(cur, dict) or cur.get("error"):
        return {"ok": False, "error": f"could not read zone: {(cur or {}).get('error', cur)}"}
    body = {"name": cur.get("name"), "policy_id": cur.get("policy_id"),
            "criticality": cur.get("criticality")}
    changed = []
    if (name or "").strip():
        body["name"] = name.strip()[:256]
        changed.append("name")
    if (policy_id or "").strip():
        if not re.match(r"^[A-Za-z0-9-]+$", policy_id.strip()):
            return {"ok": False, "error": "policy_id is not valid"}
        body["policy_id"] = policy_id.strip()
        changed.append("policy_id")
    if (criticality or "").strip():
        crit = criticality.strip().title()
        if crit not in _CRIT:
            return {"ok": False, "error": "criticality must be Low, Normal, or High"}
        body["criticality"] = crit
        changed.append("criticality")
    if not changed:
        return {"ok": False, "error": "give at least one field to change"}
    r = client.write("PUT", f"/zones/v2/{zid}", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "zone_id": zid, "changed": changed, "note": "zone updated"}
