"""Cylance threats found on a specific device (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_device_threats"
DESCRIPTION = ("List the threats Cylance has found on ONE device. Give the `device_id` (GUID). "
               "Returns each threat's name, sha256, classification, and status (allowed/quarantined). "
               "Pass `device_ids` (a list) to act on MANY devices in ONE call — do NOT call this "
               "tool once per device.")
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
    },
    "additionalProperties": False,
}
_FIELDS = ("name", "sha256", "classification", "sub_classification", "file_status",
           "cylance_score", "date_found")


def _one(ctx, device_id: str):
    did = (device_id or "").strip()
    if not re.match(r"^[A-Za-z0-9-]+$", did):
        return {"ok": False, "error": "device_id is not valid"}
    out = []
    for t in ctx.client("cylance").get_paginated(f"/devices/v2/{did}/threats"):
        out.append({k: t.get(k) for k in _FIELDS})
    return out


def _one_row(ctx, device_id: str) -> dict:
    r = _one(ctx, device_id)                           # bare list of threats, or error dict
    if isinstance(r, list):
        return {"device_id": device_id, "ok": True, "threats": r}
    return {"device_id": device_id, **r}


def run(ctx, device_id: str = "", device_ids: Any = None, **_: Any):
    wanted = [str(d).strip() for d in (device_ids or []) if str(d).strip()]
    if wanted:                                         # batch — one call, many devices
        results = [_one_row(ctx, d) for d in wanted[:200]]
        return {"ok": any(r.get("ok") for r in results), "devices_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, device_id)
