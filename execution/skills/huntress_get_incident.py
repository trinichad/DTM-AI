"""Get one Huntress incident report's detail (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "huntress_get_incident"
DESCRIPTION = ("Get the full detail for one Huntress incident report by `incident_id` — severity, "
               "status, affected agent, indicators, and any proposed remediations.")
SOURCE = "huntress"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "incident_id": {"type": "string", "description": "the Huntress incident report id"},
    },
    "required": ["incident_id"],
    "additionalProperties": False,
}


def run(ctx, incident_id: str, **_: Any):
    iid = str(incident_id or "").strip()
    if not re.match(r"^\d+$", iid):
        return {"ok": False, "error": "incident_id must be numeric"}
    return ctx.client("huntress").get(f"/incident_reports/{iid}")
