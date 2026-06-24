"""Resolve a Huntress incident report (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "huntress_resolve_incident"
DESCRIPTION = ("Resolve a Huntress incident report by `incident_id`. Optionally include a `note`. "
               "Confirm the incident first with huntress_get_incident. Pass `incident_id` for one "
               "or `incident_ids` (a list) to resolve MANY in ONE call — do NOT call this tool "
               "once per incident.")
SOURCE = "huntress"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "incident_id": {"type": "string", "description": "the Huntress incident report id"},
        "incident_ids": {"type": "array", "items": {"type": "string"},
                         "description": "resolve MANY incidents in ONE call — a list of incident "
                                        "report ids; results come back together. Use this instead "
                                        "of calling the tool once per incident."},
        "note": {"type": "string", "description": "an optional resolution note"},
    },
    "additionalProperties": False,
}


def run(ctx, incident_id: str = "", incident_ids: Any = None, note: str = "", **_: Any):
    wanted = [str(x).strip() for x in (incident_ids or []) if str(x).strip()]
    if wanted:                                         # batch (D-110) — one call, many incidents
        results = [_one(ctx, x, note) for x in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "incidents_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, incident_id, note)


def _one(ctx, incident_id: str, note: str = "") -> dict:
    iid = str(incident_id or "").strip()
    if not re.match(r"^\d+$", iid):
        return {"ok": False, "incident_id": iid, "error": "incident_id must be numeric"}
    body: dict[str, Any] = {}
    if (note or "").strip():
        body["note"] = note.strip()[:1000]
    r = ctx.client("huntress").write("POST", f"/incident_reports/{iid}/resolution", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "incident_id": iid, "error": r["error"]}
    return {"ok": True, "incident_id": iid, "result": r, "note": "incident resolved"}
