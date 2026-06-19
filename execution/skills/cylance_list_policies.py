"""Cylance policies — list (D-82)."""
from __future__ import annotations

from typing import Any

NAME = "cylance_list_policies"
DESCRIPTION = ("List the Cylance device policies for this client (id, name, device count, "
               "modified). Use cylance_get_policy for one policy's full settings.")
SOURCE = "cylance"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
_FIELDS = ("id", "name", "device_count", "zone_count", "date_modified", "policy_utc_timestamp")


def run(ctx, **_: Any):
    out = []
    for p in ctx.client("cylance").get_paginated("/policies/v2"):
        out.append({k: p.get(k) for k in _FIELDS if k in p} or p)
    return out
