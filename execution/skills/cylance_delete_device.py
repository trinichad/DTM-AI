"""Remove a device from Cylance (D-82) — DESTRUCTIVE."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_delete_device"
DESCRIPTION = ("Remove a device from the Cylance tenant (uninstalls protection from management — "
               "the endpoint stops reporting). Give the `device_id`. Pass `device_ids` (a list) to "
               "remove MANY devices in ONE call — do NOT call this tool once per device. "
               "Destructive, so it always needs a per-action approval.")
SOURCE = "cylance"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device_id": {"type": "string", "description": "the Cylance device id (GUID) to remove"},
        "device_ids": {"type": "array", "items": {"type": "string"},
                       "description": "act on MANY devices in ONE call — a list of device ids; "
                                      "results come back together. Use this instead of calling the "
                                      "tool once per device."},
    },
    "additionalProperties": False,
}


def _one(ctx, device_id: str) -> dict:
    did = (device_id or "").strip()
    if not re.match(r"^[A-Za-z0-9-]+$", did):
        return {"ok": False, "device_id": did, "error": "device_id is not valid"}
    r = ctx.client("cylance").write_destructive("DELETE", "/devices/v2", {"device_ids": [did]})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "device_id": did, "error": r["error"]}
    return {"ok": True, "device_id": did, "note": "device removed from Cylance"}


def run(ctx, device_id: str = "", device_ids: Any = None, **_: Any):
    wanted = [str(d).strip() for d in (device_ids or []) if str(d).strip()]
    if wanted:                                         # batch — one call, many devices
        results = ctx.map_progress(wanted[:200], lambda d: _one(ctx, d))
        return {"ok": any(r.get("ok") for r in results), "devices_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, device_id)
