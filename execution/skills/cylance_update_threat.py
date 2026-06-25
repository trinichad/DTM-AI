"""Waive or quarantine a threat on a Cylance device (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_update_threat"
DESCRIPTION = ("Change a threat's status on a Cylance device: `action`='quarantine' (block it) or "
               "'waive' (allow it on that device). Give the `device_id` and the threat `sha256`. "
               "Pass `device_ids` (a list) to apply the same action+sha256 to MANY devices in ONE "
               "call — do NOT call this tool once per device. To block/allow a file EVERYWHERE "
               "instead, use cylance_globallist_add.")
SOURCE = "cylance"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_ACTIONS = {"quarantine": "Quarantine", "waive": "Waive"}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device_id": {"type": "string", "description": "the Cylance device id (GUID)"},
        "device_ids": {"type": "array", "items": {"type": "string"},
                       "description": "act on MANY devices in ONE call — a list of device ids; "
                                      "results come back together. Use this instead of calling the "
                                      "tool once per device."},
        "sha256": {"type": "string", "description": "the threat's SHA-256 hash"},
        "action": {"type": "string", "enum": list(_ACTIONS), "description": "quarantine or waive"},
    },
    "required": ["sha256", "action"],
    "additionalProperties": False,
}


def _one(ctx, device_id: str, sha256: str, action: str) -> dict:
    did = (device_id or "").strip()
    h = (sha256 or "").strip().lower()
    event = _ACTIONS.get((action or "").strip().lower())
    if not re.match(r"^[A-Za-z0-9-]+$", did):
        return {"ok": False, "device_id": did, "error": "device_id is not valid"}
    if not re.match(r"^[0-9a-f]{64}$", h):
        return {"ok": False, "device_id": did, "error": "sha256 must be a 64-character hex hash"}
    if not event:
        return {"ok": False, "device_id": did, "error": "action must be 'quarantine' or 'waive'"}
    r = ctx.client("cylance").write("PUT", f"/devices/v2/{did}/threats",
                                    {"threat_id": h, "event": event})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "device_id": did, "error": r["error"]}
    return {"ok": True, "device_id": did, "sha256": h, "action": event, "note": "threat updated"}


def run(ctx, device_id: str = "", sha256: str = "", action: str = "",
        device_ids: Any = None, **_: Any):
    wanted = [str(d).strip() for d in (device_ids or []) if str(d).strip()]
    if wanted:                                         # batch — one call, many devices
        results = ctx.map_progress(wanted[:200], lambda d: _one(ctx, d, sha256, action))
        return {"ok": any(r.get("ok") for r in results), "devices_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, device_id, sha256, action)
