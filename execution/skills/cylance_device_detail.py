"""Cylance device detail — full record for one endpoint (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_device_detail"
DESCRIPTION = ("Get the full Cylance record for ONE device — by `device_id` or by `hostname`. "
               "Returns policy, zones, agent version, OS, state, last user, IP/MAC, etc. Pass "
               "`device_ids` (a list) to inspect MANY devices in ONE call — do NOT call this tool "
               "once per device.")
SOURCE = "cylance"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device_id": {"type": "string", "description": "the Cylance device id (GUID)"},
        "device_ids": {"type": "array", "items": {"type": "string"},
                       "description": "act on MANY devices in ONE call — a list of device ids; "
                                      "results come back together. Use this instead of calling the "
                                      "tool once per device."},
        "hostname": {"type": "string", "description": "the device hostname (instead of device_id)"},
    },
    "additionalProperties": False,
}


def _one(ctx, device_id: str, hostname: str) -> dict:
    did = (device_id or "").strip()
    host = (hostname or "").strip()
    if did:
        if not re.match(r"^[A-Za-z0-9-]+$", did):
            return {"ok": False, "device_id": did, "error": "device_id is not valid"}
        return ctx.client("cylance").get(f"/devices/v2/{did}")
    if host:
        if not re.match(r"^[A-Za-z0-9._-]+$", host):
            return {"ok": False, "error": "hostname is not valid"}
        return ctx.client("cylance").get(f"/devices/v2/hostname/{host}")
    return {"ok": False, "error": "give a device_id or a hostname"}


def run(ctx, device_id: str = "", hostname: str = "", device_ids: Any = None, **_: Any):
    wanted = [str(d).strip() for d in (device_ids or []) if str(d).strip()]
    if wanted:                                         # batch — one call, many devices
        results = ctx.map_progress(wanted[:200], lambda d: _one(ctx, d, ""))
        return {"ok": any(r.get("ok") for r in results), "devices_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, device_id, hostname)
