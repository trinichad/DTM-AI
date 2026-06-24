"""Assign a Cylance policy to a device (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_assign_policy"
DESCRIPTION = ("Assign a Cylance device policy to a device. Give the `device_id` and the "
               "`policy_id` to apply (use cylance_list_policies / cylance_list_devices to find "
               "them). The device keeps its name; only the policy changes. Pass `device_ids` (a "
               "list) to assign the SAME policy to MANY devices in ONE call — do NOT call this tool "
               "once per device.")
SOURCE = "cylance"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device_id": {"type": "string", "description": "the Cylance device id (GUID)"},
        "device_ids": {"type": "array", "items": {"type": "string"},
                       "description": "act on MANY devices in ONE call — a list of device ids; "
                                      "results come back together. Use this instead of calling the "
                                      "tool once per device."},
        "policy_id": {"type": "string", "description": "the policy id to assign"},
    },
    "required": ["policy_id"],
    "additionalProperties": False,
}


def _one(ctx, device_id: str, policy_id: str) -> dict:
    did = (device_id or "").strip()
    pid = (policy_id or "").strip()
    if not re.match(r"^[A-Za-z0-9-]+$", did):
        return {"ok": False, "device_id": did, "error": "device_id is not valid"}
    if not re.match(r"^[A-Za-z0-9-]+$", pid):
        return {"ok": False, "device_id": did, "error": "policy_id is not valid"}
    client = ctx.client("cylance")
    dev = client.get(f"/devices/v2/{did}")          # PUT requires the current name
    if not isinstance(dev, dict) or dev.get("error"):
        return {"ok": False, "device_id": did,
                "error": f"could not read device: {(dev or {}).get('error', dev)}"}
    name = dev.get("name") or dev.get("host_name")
    r = client.write("PUT", f"/devices/v2/{did}", {"name": name, "policy_id": pid})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "device_id": did, "error": r["error"]}
    return {"ok": True, "device_id": did, "policy_id": pid, "device_name": name,
            "note": "policy assigned"}


def run(ctx, device_id: str = "", policy_id: str = "", device_ids: Any = None, **_: Any):
    wanted = [str(d).strip() for d in (device_ids or []) if str(d).strip()]
    if wanted:                                         # batch — one policy, many devices
        results = [_one(ctx, d, policy_id) for d in wanted[:200]]
        return {"ok": any(r.get("ok") for r in results), "devices_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, device_id, policy_id)
