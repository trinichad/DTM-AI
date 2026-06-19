"""Get one Huntress escalation's detail (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "huntress_get_escalation"
DESCRIPTION = "Get the full detail for one Huntress escalation by `escalation_id`."
SOURCE = "huntress"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "escalation_id": {"type": "string", "description": "the Huntress escalation id"},
    },
    "required": ["escalation_id"],
    "additionalProperties": False,
}


def run(ctx, escalation_id: str, **_: Any):
    eid = str(escalation_id or "").strip()
    if not re.match(r"^\d+$", eid):
        return {"ok": False, "error": "escalation_id must be numeric"}
    return ctx.client("huntress").get(f"/escalations/{eid}")
