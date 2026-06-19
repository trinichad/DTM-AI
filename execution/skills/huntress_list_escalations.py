"""List Huntress escalations (D-82)."""
from __future__ import annotations

from typing import Any

NAME = "huntress_list_escalations"
DESCRIPTION = ("List Huntress escalations — items the Huntress SOC has escalated for action, with "
               "status and the related agent/organization. Use huntress_get_escalation for detail "
               "and huntress_resolve_escalation to close common ones.")
SOURCE = "huntress"
CATEGORY = "alert"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "description": "filter by status, e.g. 'open' (optional)"},
    },
    "additionalProperties": False,
}
_FIELDS = ("id", "status", "summary", "escalation_type", "organization_id", "agent_id",
           "created_at", "updated_at")


def run(ctx, status: str = "", **_: Any):
    params = {}
    if (status or "").strip():
        params["status"] = status.strip()
    out = []
    for e in ctx.client("huntress").get_paginated("/escalations", params or None):
        out.append({k: e.get(k) for k in _FIELDS if k in e} or e)
    return out
