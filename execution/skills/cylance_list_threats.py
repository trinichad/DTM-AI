"""List Cylance threats (trimmed payload)."""
from __future__ import annotations

from typing import Any

NAME = "cylance_list_threats"
DESCRIPTION = ("List threats detected by Cylance for this client. "
               "Returns sha256, name, classification, file_status, cylance_score, last_found.")
SOURCE = "cylance"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}

_FIELDS = ("sha256", "name", "classification", "sub_classification", "file_status",
           "cylance_score", "global_quarantined", "last_found")


def run(ctx, **_: Any):
    return [{k: t.get(k) for k in _FIELDS}
            for t in ctx.client("cylance").get_paginated("/threats/v2")]
