"""Get one UniFi device's full detail (D-84)."""
from __future__ import annotations

import re
from typing import Any

from . import _unifi_common as _u

NAME = "unifi_device_detail"
DESCRIPTION = ("Get the full detail for one UniFi device by `device_id` — ports, radios, uplink, "
               "uptime, and configuration. Pass `device_ids` (a list) to inspect MANY devices in "
               "ONE call — do NOT call this tool once per device. Optional `site`.")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "read"
RISK_LEVEL = "low"
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
    return client.get(f"/v1/sites/{sid}/devices/{did}")


def run(ctx, device_id: str = "", site: str = "", device_ids: Any = None, **_: Any):
    wanted = [str(d).strip() for d in (device_ids or []) if str(d).strip()]
    if wanted:                                         # batch — one call, many devices
        results = ctx.map_progress(wanted[:200], lambda d: _one(ctx, d, site))
        return {"ok": any(r.get("ok") for r in results), "devices_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, device_id, site)
