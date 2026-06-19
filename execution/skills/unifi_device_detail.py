"""Get one UniFi device's full detail (D-84)."""
from __future__ import annotations

import re
from typing import Any

from . import _unifi_common as _u

NAME = "unifi_device_detail"
DESCRIPTION = ("Get the full detail for one UniFi device by `device_id` — ports, radios, uplink, "
               "uptime, and configuration. Optional `site`.")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "read"
RISK_LEVEL = "low"
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
    return client.get(f"/v1/sites/{sid}/devices/{did}")
