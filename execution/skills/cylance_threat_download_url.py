"""Cylance threat sample download URL (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_threat_download_url"
DESCRIPTION = ("Get a time-limited download URL for a quarantined threat sample, by its `sha256`, "
               "so it can be retrieved for analysis (the file comes back password-protected as a "
               "zip by Cylance). Returns the presigned URL.")
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


def run(ctx, sha256: str, **_: Any):
    h = (sha256 or "").strip().lower()
    if not re.match(r"^[0-9a-f]{64}$", h):
        return {"ok": False, "error": "sha256 must be a 64-character hex hash"}
    return ctx.client("cylance").get(f"/threats/v2/download/{h}")
