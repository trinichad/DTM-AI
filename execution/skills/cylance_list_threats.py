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
    # Dedup by sha256: Cylance pagination drifts, so boundary records repeat across pages and a raw
    # count over-reports. Unique sha256 is the authoritative count (see cylance_list_devices).
    seen: set = set()
    out: list[dict] = []
    for t in ctx.client("cylance").get_paginated("/threats/v2"):
        key = t.get("sha256")
        if key is not None and key in seen:
            continue
        if key is not None:
            seen.add(key)
        out.append({k: t.get(k) for k in _FIELDS})
    return out
