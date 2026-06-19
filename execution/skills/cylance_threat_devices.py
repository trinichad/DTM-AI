"""Cylance devices affected by a threat (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_threat_devices"
DESCRIPTION = ("List the devices a given threat (`sha256`) was found on, with its status on each — "
               "the 'how far did this spread?' view.")
SOURCE = "cylance"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sha256": {"type": "string", "description": "the threat's SHA-256 hash"},
    },
    "required": ["sha256"],
    "additionalProperties": False,
}
_FIELDS = ("id", "name", "state", "file_status", "file_path", "agent_version", "policy_id")


def run(ctx, sha256: str, **_: Any):
    h = (sha256 or "").strip().lower()
    if not re.match(r"^[0-9a-f]{64}$", h):
        return {"ok": False, "error": "sha256 must be a 64-character hex hash"}
    out = []
    for d in ctx.client("cylance").get_paginated(f"/threats/v2/{h}/devices"):
        out.append({k: d.get(k) for k in _FIELDS if k in d} or d)
    return out
