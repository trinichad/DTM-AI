"""Create a Cylance zone (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_create_zone"
DESCRIPTION = ("Create a Cylance zone (a device group tied to a policy). Give the `name`, the "
               "`policy_id` to attach, and `criticality` (Low/Normal/High).")
SOURCE = "cylance"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_CRIT = ("Low", "Normal", "High")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "the zone name"},
        "policy_id": {"type": "string", "description": "the policy id to attach to the zone"},
        "criticality": {"type": "string", "enum": list(_CRIT), "description": "Low/Normal/High"},
    },
    "required": ["name", "policy_id", "criticality"],
    "additionalProperties": False,
}


def run(ctx, name: str, policy_id: str, criticality: str, **_: Any):
    nm = (name or "").strip()
    pid = (policy_id or "").strip()
    crit = (criticality or "").strip().title()
    if not nm:
        return {"ok": False, "error": "give a zone name"}
    if not re.match(r"^[A-Za-z0-9-]+$", pid):
        return {"ok": False, "error": "policy_id is not valid"}
    if crit not in _CRIT:
        return {"ok": False, "error": "criticality must be Low, Normal, or High"}
    r = ctx.client("cylance").write("POST", "/zones/v2",
                                    {"name": nm[:256], "policy_id": pid, "criticality": crit})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "zone": nm, "policy_id": pid, "created": r, "note": "zone created"}
