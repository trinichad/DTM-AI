"""Get one Huntress organization's detail (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "huntress_get_organization"
DESCRIPTION = "Get the full detail for one Huntress organization by `organization_id`."
SOURCE = "huntress"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "organization_id": {"type": "string", "description": "the Huntress organization id"},
    },
    "required": ["organization_id"],
    "additionalProperties": False,
}


def run(ctx, organization_id: str, **_: Any):
    oid = str(organization_id or "").strip()
    if not re.match(r"^\d+$", oid):
        return {"ok": False, "error": "organization_id must be numeric"}
    return ctx.client("huntress").get(f"/organizations/{oid}")
