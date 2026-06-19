"""Restart a UniFi device (D-84)."""
from __future__ import annotations

import re
from typing import Any

from . import _unifi_common as _u

NAME = "unifi_restart_device"
DESCRIPTION = ("Restart (reboot) a UniFi device — an AP, switch, or gateway — by `device_id`. The "
               "device drops off the network for ~1-2 minutes while it reboots. Optional `site`.")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "write"
RISK_LEVEL = "high"          # brief outage for whatever the device serves
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device_id": {"type": "string", "description": "the UniFi device id"},
        "site": {"type": "string", "description": "site name or id (optional)"},
    },
    "required": ["device_id"],
    "additionalProperties": False,
}


def run(ctx, device_id: str, site: str = "", **_: Any):
    did = (device_id or "").strip()
    if not re.match(r"^[A-Za-z0-9-]+$", did):
        return {"ok": False, "error": "device_id is not valid"}
    client = ctx.client("unifi")
    sid, err = _u.resolve_site(client, site)
    if err:
        return {"ok": False, "error": err}
    r = client.write("POST", f"/v1/sites/{sid}/devices/{did}/actions", {"action": "RESTART"})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "device_id": did, "note": "restart submitted — device reboots in ~1-2 min"}
