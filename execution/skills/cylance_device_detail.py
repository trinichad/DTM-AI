"""Cylance device detail — full record for one endpoint (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_device_detail"
DESCRIPTION = ("Get the full Cylance record for ONE device — by `device_id` or by `hostname`. "
               "Returns policy, zones, agent version, OS, state, last user, IP/MAC, etc.")
SOURCE = "cylance"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device_id": {"type": "string", "description": "the Cylance device id (GUID)"},
        "hostname": {"type": "string", "description": "the device hostname (instead of device_id)"},
    },
    "additionalProperties": False,
}


def run(ctx, device_id: str = "", hostname: str = "", **_: Any):
    did = (device_id or "").strip()
    host = (hostname or "").strip()
    if did:
        if not re.match(r"^[A-Za-z0-9-]+$", did):
            return {"ok": False, "error": "device_id is not valid"}
        return ctx.client("cylance").get(f"/devices/v2/{did}")
    if host:
        if not re.match(r"^[A-Za-z0-9._-]+$", host):
            return {"ok": False, "error": "hostname is not valid"}
        return ctx.client("cylance").get(f"/devices/v2/hostname/{host}")
    return {"ok": False, "error": "give a device_id or a hostname"}
