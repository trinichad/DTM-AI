"""List Huntress organizations (D-82)."""
from __future__ import annotations

from typing import Any

NAME = "huntress_list_organizations"
DESCRIPTION = ("List the Huntress organizations under this account (id, name, agent counts, "
               "status). Pass name_contains to filter by name.")
SOURCE = "huntress"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name_contains": {"type": "string", "description": "case-insensitive name substring filter"},
    },
    "additionalProperties": False,
}
_FIELDS = ("id", "name", "status", "agents_count", "incident_reports_count", "created_at")


def run(ctx, name_contains: str = "", **_: Any):
    needle = (name_contains or "").strip().lower()
    out = []
    for o in ctx.client("huntress").get_paginated("/organizations"):
        if needle and needle not in str(o.get("name", "")).lower():
            continue
        out.append({k: o.get(k) for k in _FIELDS if k in o} or o)
    return out
