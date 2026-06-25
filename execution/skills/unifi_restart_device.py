"""Restart a UniFi device (D-84)."""
from __future__ import annotations

import re
from typing import Any

from . import _unifi_common as _u

NAME = "unifi_restart_device"
DESCRIPTION = ("Restart (reboot) a UniFi device — an AP, switch, or gateway — by `device_id`. The "
               "device drops off the network for ~1-2 minutes while it reboots. Pass `device_ids` "
               "(a list) to restart MANY devices in ONE call — do NOT call this tool once per "
               "device. Optional `site`.")
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
        "device_ids": {"type": "array", "items": {"type": "string"},
                       "description": "act on MANY devices in ONE call — a list of device ids; "
                                      "results come back together. Use this instead of calling the "
                                      "tool once per device."},
        "site": {"type": "string", "description": "site name or id (optional)"},
    },
    "additionalProperties": False,
}


def _one(ctx, device_id: str, site: str) -> dict:
    did = (device_id or "").strip()
    if not re.match(r"^[A-Za-z0-9-]+$", did):
        return {"ok": False, "device_id": did, "error": "device_id is not valid"}
    client = ctx.client("unifi")
    sid, err = _u.resolve_site(client, site)
    if err:
        return {"ok": False, "device_id": did, "error": err}
    r = client.write("POST", f"/v1/sites/{sid}/devices/{did}/actions", {"action": "RESTART"})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "device_id": did, "error": r["error"]}
    return {"ok": True, "device_id": did, "note": "restart submitted — device reboots in ~1-2 min"}


def run(ctx, device_id: str = "", site: str = "", device_ids: Any = None, **_: Any):
    wanted = [str(d).strip() for d in (device_ids or []) if str(d).strip()]
    if wanted:                                         # batch — one call, many devices
        results = ctx.map_progress(wanted[:200], lambda d: _one(ctx, d, site))
        return {"ok": any(r.get("ok") for r in results), "devices_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, device_id, site)
