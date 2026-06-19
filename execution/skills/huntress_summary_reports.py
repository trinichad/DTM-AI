"""List Huntress summary reports (D-82)."""
from __future__ import annotations

from typing import Any

NAME = "huntress_summary_reports"
DESCRIPTION = ("List Huntress summary reports for this account (monthly/weekly roll-ups of agents, "
               "incidents, and activity). Returns id, type, period, organization, and status.")
SOURCE = "huntress"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
_FIELDS = ("id", "type", "report_type", "period", "organization_id", "status", "created_at")


def run(ctx, **_: Any):
    out = []
    for r in ctx.client("huntress").get_paginated("/summary_reports"):
        out.append({k: r.get(k) for k in _FIELDS if k in r} or r)
    return out
