"""Power-cycle a UniFi switch PoE port (D-84)."""
from __future__ import annotations

import re
from typing import Any

from . import _unifi_common as _u

NAME = "unifi_port_cycle"
DESCRIPTION = ("Power-cycle a PoE port on a UniFi switch — turns the port's power off and back on, "
               "to reboot a stuck PoE device (AP, camera, phone) without touching it. Give the "
               "switch `device_id` and the `port` number. Pass `device_ids` (a list) to power-cycle "
               "the same `port` on MANY switches in ONE call — do NOT call this tool once per "
               "device. Optional `site`.")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "write"
RISK_LEVEL = "high"          # drops the device on that port
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device_id": {"type": "string", "description": "the UniFi switch's device id"},
        "device_ids": {"type": "array", "items": {"type": "string"},
                       "description": "act on MANY devices in ONE call — a list of device ids; "
                                      "results come back together. Use this instead of calling the "
                                      "tool once per device."},
        "port": {"type": "integer", "minimum": 1, "maximum": 64, "description": "the port number"},
        "site": {"type": "string", "description": "site name or id (optional)"},
    },
    "required": ["port"],
    "additionalProperties": False,
}


def _one(ctx, device_id: str, port: int, site: str) -> dict:
    did = (device_id or "").strip()
    if not re.match(r"^[A-Za-z0-9-]+$", did):
        return {"ok": False, "device_id": did, "error": "device_id is not valid"}
    p = int(port)
    if not 1 <= p <= 64:
        return {"ok": False, "device_id": did, "error": "port must be between 1 and 64"}
    client = ctx.client("unifi")
    sid, err = _u.resolve_site(client, site)
    if err:
        return {"ok": False, "device_id": did, "error": err}
    path = f"/v1/sites/{sid}/devices/{did}/interfaces/ports/{p}/actions"
    r = client.write("POST", path, {"action": "POWER_CYCLE"})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "device_id": did, "error": r["error"]}
    return {"ok": True, "device_id": did, "port": p, "note": "port power-cycled"}


def run(ctx, device_id: str = "", port: int = 0, site: str = "", device_ids: Any = None, **_: Any):
    wanted = [str(d).strip() for d in (device_ids or []) if str(d).strip()]
    if wanted:                                         # batch — same port, many switches
        results = [_one(ctx, d, port, site) for d in wanted[:200]]
        return {"ok": any(r.get("ok") for r in results), "devices_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, device_id, port, site)
