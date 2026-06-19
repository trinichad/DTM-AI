"""Resolve a Huntress incident report (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "huntress_resolve_incident"
DESCRIPTION = ("Resolve a Huntress incident report by `incident_id`. Optionally include a `note`. "
               "Confirm the incident first with huntress_get_incident.")
SOURCE = "huntress"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "incident_id": {"type": "string", "description": "the Huntress incident report id"},
        "note": {"type": "string", "description": "an optional resolution note"},
    },
    "required": ["incident_id"],
    "additionalProperties": False,
}


def run(ctx, incident_id: str, note: str = "", **_: Any):
    iid = str(incident_id or "").strip()
    if not re.match(r"^\d+$", iid):
        return {"ok": False, "error": "incident_id must be numeric"}
    body: dict[str, Any] = {}
    if (note or "").strip():
        body["note"] = note.strip()[:1000]
    r = ctx.client("huntress").write("POST", f"/incident_reports/{iid}/resolution", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "incident_id": iid, "result": r, "note": "incident resolved"}
