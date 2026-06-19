"""List Huntress incident reports (trimmed payload)."""
from __future__ import annotations

from typing import Any

NAME = "huntress_list_incidents"
DESCRIPTION = ("List Huntress incident reports for this client. "
               "Returns id, status, severity, summary, agent_id, sent_at.")
SOURCE = "huntress"
CATEGORY = "alert"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"status": {"type": "string", "enum": ["sent", "closed", "dismissed", "all"]}},
    "additionalProperties": False,
}

_FIELDS = ("id", "status", "severity", "summary", "agent_id", "organization_id", "sent_at",
           "remediations", "indicator_types")


def run(ctx, status: str = "all", **_: Any):
    params = None if status == "all" else {"status": status}
    rows = ctx.client("huntress").get_paginated("/incident_reports", params)
    return [{k: r.get(k) for k in _FIELDS} for r in rows]
