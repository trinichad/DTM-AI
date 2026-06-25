"""Forget (remove) a UniFi device from a site (D-84) — destructive."""
from __future__ import annotations

import re
from typing import Any

from . import _unifi_common as _u

NAME = "unifi_forget_device"
DESCRIPTION = ("Forget (un-adopt / remove) a UniFi device from the site by `device_id` — it stops "
               "being managed and returns to a factory-pending state. Pass `device_ids` (a list) "
               "to forget MANY devices in ONE call — do NOT call this tool once per device. "
               "Destructive, so it always needs a per-action approval. Optional `site`.")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device_id": {"type": "string", "description": "the UniFi device id to forget"},
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
    r = client.write_destructive("DELETE", f"/v1/sites/{sid}/devices/{did}", None)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "device_id": did, "error": r["error"]}
    return {"ok": True, "device_id": did, "note": "device forgotten"}


def run(ctx, device_id: str = "", site: str = "", device_ids: Any = None, **_: Any):
    wanted = [str(d).strip() for d in (device_ids or []) if str(d).strip()]
    if wanted:                                         # batch — one call, many devices
        results = ctx.map_progress(wanted[:200], lambda d: _one(ctx, d, site))
        return {"ok": any(r.get("ok") for r in results), "devices_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, device_id, site)
