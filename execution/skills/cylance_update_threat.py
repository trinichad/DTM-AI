"""Waive or quarantine a threat on a Cylance device (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_update_threat"
DESCRIPTION = ("Change a threat's status on a Cylance device: `action`='quarantine' (block it) or "
               "'waive' (allow it on that device). Give the `device_id` and the threat `sha256`. "
               "To block/allow a file EVERYWHERE instead, use cylance_globallist_add.")
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
        "sha256": {"type": "string", "description": "the threat's SHA-256 hash"},
        "action": {"type": "string", "enum": list(_ACTIONS), "description": "quarantine or waive"},
    },
    "required": ["device_id", "sha256", "action"],
    "additionalProperties": False,
}


def run(ctx, device_id: str, sha256: str, action: str, **_: Any):
    did = (device_id or "").strip()
    h = (sha256 or "").strip().lower()
    event = _ACTIONS.get((action or "").strip().lower())
    if not re.match(r"^[A-Za-z0-9-]+$", did):
        return {"ok": False, "error": "device_id is not valid"}
    if not re.match(r"^[0-9a-f]{64}$", h):
        return {"ok": False, "error": "sha256 must be a 64-character hex hash"}
    if not event:
        return {"ok": False, "error": "action must be 'quarantine' or 'waive'"}
    r = ctx.client("cylance").write("PUT", f"/devices/v2/{did}/threats",
                                    {"threat_id": h, "event": event})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "device_id": did, "sha256": h, "action": event, "note": "threat updated"}
