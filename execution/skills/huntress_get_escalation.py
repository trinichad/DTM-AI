"""Get one Huntress escalation's detail (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "huntress_get_escalation"
DESCRIPTION = ("Get the full detail for one Huntress escalation by `escalation_id`. Pass "
               "`escalation_id` for one or `escalation_ids` (a list) to fetch MANY in ONE call — "
               "do NOT call this tool once per escalation.")
SOURCE = "huntress"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "escalation_id": {"type": "string", "description": "the Huntress escalation id"},
        "escalation_ids": {"type": "array", "items": {"type": "string"},
                           "description": "fetch MANY escalations in ONE call — a list of "
                                          "escalation ids; results come back together. Use this "
                                          "instead of calling the tool once per escalation."},
    },
    "additionalProperties": False,
}


def run(ctx, escalation_id: str = "", escalation_ids: Any = None, **_: Any):
    wanted = [str(x).strip() for x in (escalation_ids or []) if str(x).strip()]
    if wanted:                                         # batch (D-110) — one call, many escalations
        results = ctx.map_progress(wanted[:500], lambda x: _one(ctx, x))
        return {"ok": any(r.get("ok") for r in results), "escalations_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, escalation_id)


def _one(ctx, escalation_id: str) -> dict:
    eid = str(escalation_id or "").strip()
    if not re.match(r"^\d+$", eid):
        return {"ok": False, "escalation_id": eid, "error": "escalation_id must be numeric"}
    return ctx.client("huntress").get(f"/escalations/{eid}")
