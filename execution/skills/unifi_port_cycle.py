"""Power-cycle a UniFi switch PoE port (D-84)."""
from __future__ import annotations

import re
from typing import Any

from . import _unifi_common as _u

NAME = "unifi_port_cycle"
DESCRIPTION = ("Power-cycle a PoE port on a UniFi switch — turns the port's power off and back on, "
               "to reboot a stuck PoE device (AP, camera, phone) without touching it. Give the "
               "switch `device_id` and the `port` number. Optional `site`.")
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
        "port": {"type": "integer", "minimum": 1, "maximum": 64, "description": "the port number"},
        "site": {"type": "string", "description": "site name or id (optional)"},
    },
    "required": ["device_id", "port"],
    "additionalProperties": False,
}


def run(ctx, device_id: str, port: int, site: str = "", **_: Any):
    did = (device_id or "").strip()
    if not re.match(r"^[A-Za-z0-9-]+$", did):
        return {"ok": False, "error": "device_id is not valid"}
    p = int(port)
    if not 1 <= p <= 64:
        return {"ok": False, "error": "port must be between 1 and 64"}
    client = ctx.client("unifi")
    sid, err = _u.resolve_site(client, site)
    if err:
        return {"ok": False, "error": err}
    path = f"/v1/sites/{sid}/devices/{did}/interfaces/ports/{p}/actions"
    r = client.write("POST", path, {"action": "POWER_CYCLE"})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "device_id": did, "port": p, "note": "port power-cycled"}
