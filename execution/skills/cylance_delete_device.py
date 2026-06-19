"""Remove a device from Cylance (D-82) — DESTRUCTIVE."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_delete_device"
DESCRIPTION = ("Remove a device from the Cylance tenant (uninstalls protection from management — "
               "the endpoint stops reporting). Give the `device_id`. Destructive, so it always "
               "needs a per-action approval.")
SOURCE = "cylance"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device_id": {"type": "string", "description": "the Cylance device id (GUID) to remove"},
    },
    "required": ["device_id"],
    "additionalProperties": False,
}


def run(ctx, device_id: str, **_: Any):
    did = (device_id or "").strip()
    if not re.match(r"^[A-Za-z0-9-]+$", did):
        return {"ok": False, "error": "device_id is not valid"}
    r = ctx.client("cylance").write_destructive("DELETE", "/devices/v2", {"device_ids": [did]})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "device_id": did, "note": "device removed from Cylance"}
