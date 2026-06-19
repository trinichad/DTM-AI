"""Assign a Cylance policy to a device (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_assign_policy"
DESCRIPTION = ("Assign a Cylance device policy to a device. Give the `device_id` and the "
               "`policy_id` to apply (use cylance_list_policies / cylance_list_devices to find "
               "them). The device keeps its name; only the policy changes.")
SOURCE = "cylance"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device_id": {"type": "string", "description": "the Cylance device id (GUID)"},
        "policy_id": {"type": "string", "description": "the policy id to assign"},
    },
    "required": ["device_id", "policy_id"],
    "additionalProperties": False,
}


def run(ctx, device_id: str, policy_id: str, **_: Any):
    did = (device_id or "").strip()
    pid = (policy_id or "").strip()
    if not re.match(r"^[A-Za-z0-9-]+$", did):
        return {"ok": False, "error": "device_id is not valid"}
    if not re.match(r"^[A-Za-z0-9-]+$", pid):
        return {"ok": False, "error": "policy_id is not valid"}
    client = ctx.client("cylance")
    dev = client.get(f"/devices/v2/{did}")          # PUT requires the current name
    if not isinstance(dev, dict) or dev.get("error"):
        return {"ok": False, "error": f"could not read device: {(dev or {}).get('error', dev)}"}
    name = dev.get("name") or dev.get("host_name")
    r = client.write("PUT", f"/devices/v2/{did}", {"name": name, "policy_id": pid})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "device_id": did, "policy_id": pid, "device_name": name,
            "note": "policy assigned"}
