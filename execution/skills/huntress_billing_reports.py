"""List Huntress billing reports (D-82)."""
from __future__ import annotations

from typing import Any

NAME = "huntress_billing_reports"
DESCRIPTION = ("List Huntress billing reports for this account (per-period billed agent counts and "
               "amounts) — useful for reconciling Huntress charges against client seats.")
SOURCE = "huntress"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
_FIELDS = ("id", "status", "period", "quantity", "amount", "amount_due", "organization_id",
           "created_at")


def run(ctx, **_: Any):
    out = []
    for r in ctx.client("huntress").get_paginated("/billing_reports"):
        out.append({k: r.get(k) for k in _FIELDS if k in r} or r)
    return out
