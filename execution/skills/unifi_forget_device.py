"""Forget (remove) a UniFi device from a site (D-84) — destructive."""
from __future__ import annotations

import re
from typing import Any

from . import _unifi_common as _u

NAME = "unifi_forget_device"
DESCRIPTION = ("Forget (un-adopt / remove) a UniFi device from the site by `device_id` — it stops "
               "being managed and returns to a factory-pending state. Destructive, so it always "
               "needs a per-action approval. Optional `site`.")
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
    r = client.write_destructive("DELETE", f"/v1/sites/{sid}/devices/{did}", None)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "device_id": did, "note": "device forgotten"}
