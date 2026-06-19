"""Cylance threats found on a specific device (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_device_threats"
DESCRIPTION = ("List the threats Cylance has found on ONE device. Give the `device_id` (GUID). "
               "Returns each threat's name, sha256, classification, and status (allowed/quarantined).")
SOURCE = "cylance"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device_id": {"type": "string", "description": "the Cylance device id (GUID)"},
    },
    "required": ["device_id"],
    "additionalProperties": False,
}
_FIELDS = ("name", "sha256", "classification", "sub_classification", "file_status",
           "cylance_score", "date_found")


def run(ctx, device_id: str, **_: Any):
    did = (device_id or "").strip()
    if not re.match(r"^[A-Za-z0-9-]+$", did):
        return {"ok": False, "error": "device_id is not valid"}
    out = []
    for t in ctx.client("cylance").get_paginated(f"/devices/v2/{did}/threats"):
        out.append({k: t.get(k) for k in _FIELDS})
    return out
