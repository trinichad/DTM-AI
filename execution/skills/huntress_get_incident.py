"""Get one Huntress incident report's detail (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "huntress_get_incident"
DESCRIPTION = ("Get the full detail for one Huntress incident report by `incident_id` — severity, "
               "status, affected agent, indicators, and any proposed remediations. Pass "
               "`incident_id` for one or `incident_ids` (a list) to fetch MANY in ONE call — do "
               "NOT call this tool once per incident.")
SOURCE = "huntress"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "incident_id": {"type": "string", "description": "the Huntress incident report id"},
        "incident_ids": {"type": "array", "items": {"type": "string"},
                         "description": "fetch MANY incidents in ONE call — a list of incident "
                                        "report ids; results come back together. Use this instead "
                                        "of calling the tool once per incident."},
    },
    "additionalProperties": False,
}


def run(ctx, incident_id: str = "", incident_ids: Any = None, **_: Any):
    wanted = [str(x).strip() for x in (incident_ids or []) if str(x).strip()]
    if wanted:                                         # batch (D-110) — one call, many incidents
        results = [_one(ctx, x) for x in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "incidents_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, incident_id)


def _one(ctx, incident_id: str) -> dict:
    iid = str(incident_id or "").strip()
    if not re.match(r"^\d+$", iid):
        return {"ok": False, "incident_id": iid, "error": "incident_id must be numeric"}
    return ctx.client("huntress").get(f"/incident_reports/{iid}")
