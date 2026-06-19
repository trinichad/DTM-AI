"""Resolve a Huntress escalation (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "huntress_resolve_escalation"
DESCRIPTION = ("Resolve a Huntress escalation by `escalation_id` (for the common, self-resolvable "
               "escalations). Optionally include a `note`. Confirm the escalation first with "
               "huntress_get_escalation.")
SOURCE = "huntress"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "escalation_id": {"type": "string", "description": "the Huntress escalation id"},
        "note": {"type": "string", "description": "an optional resolution note"},
    },
    "required": ["escalation_id"],
    "additionalProperties": False,
}


def run(ctx, escalation_id: str, note: str = "", **_: Any):
    eid = str(escalation_id or "").strip()
    if not re.match(r"^\d+$", eid):
        return {"ok": False, "error": "escalation_id must be numeric"}
    body: dict[str, Any] = {}
    if (note or "").strip():
        body["note"] = note.strip()[:1000]
    r = ctx.client("huntress").write("POST", f"/escalations/{eid}/resolution", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "escalation_id": eid, "result": r, "note": "escalation resolved"}
