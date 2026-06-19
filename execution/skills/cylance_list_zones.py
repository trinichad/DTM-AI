"""Cylance zones — list (D-82)."""
from __future__ import annotations

from typing import Any

NAME = "cylance_list_zones"
DESCRIPTION = ("List the Cylance zones for this client (id, name, criticality, policy_id, device "
               "count). Zones group devices and tie them to a policy.")
SOURCE = "cylance"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
_FIELDS = ("id", "name", "criticality", "policy_id", "zone_rule_id", "update_type",
           "date_modified")


def run(ctx, **_: Any):
    out = []
    for z in ctx.client("cylance").get_paginated("/zones/v2"):
        out.append({k: z.get(k) for k in _FIELDS if k in z} or z)
    return out
