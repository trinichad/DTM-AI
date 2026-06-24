"""Resolve a Huntress escalation (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "huntress_resolve_escalation"
DESCRIPTION = ("Resolve a Huntress escalation by `escalation_id` (for the common, self-resolvable "
               "escalations). Optionally include a `note`. Confirm the escalation first with "
               "huntress_get_escalation. Pass `escalation_id` for one or `escalation_ids` (a list) "
               "to resolve MANY in ONE call — do NOT call this tool once per escalation.")
SOURCE = "huntress"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "escalation_id": {"type": "string", "description": "the Huntress escalation id"},
        "escalation_ids": {"type": "array", "items": {"type": "string"},
                           "description": "resolve MANY escalations in ONE call — a list of "
                                          "escalation ids; results come back together. Use this "
                                          "instead of calling the tool once per escalation."},
        "note": {"type": "string", "description": "an optional resolution note"},
    },
    "additionalProperties": False,
}


def run(ctx, escalation_id: str = "", escalation_ids: Any = None, note: str = "", **_: Any):
    wanted = [str(x).strip() for x in (escalation_ids or []) if str(x).strip()]
    if wanted:                                         # batch (D-110) — one call, many escalations
        results = [_one(ctx, x, note) for x in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "escalations_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, escalation_id, note)


def _one(ctx, escalation_id: str, note: str = "") -> dict:
    eid = str(escalation_id or "").strip()
    if not re.match(r"^\d+$", eid):
        return {"ok": False, "escalation_id": eid, "error": "escalation_id must be numeric"}
    body: dict[str, Any] = {}
    if (note or "").strip():
        body["note"] = note.strip()[:1000]
    r = ctx.client("huntress").write("POST", f"/escalations/{eid}/resolution", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "escalation_id": eid, "error": r["error"]}
    return {"ok": True, "escalation_id": eid, "result": r, "note": "escalation resolved"}
