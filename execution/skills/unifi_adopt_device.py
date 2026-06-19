"""Adopt a pending UniFi device (D-84)."""
from __future__ import annotations

import re
from typing import Any

from . import _unifi_common as _u

NAME = "unifi_adopt_device"
DESCRIPTION = ("Adopt a pending UniFi device onto a site (find candidates with "
               "unifi_pending_devices). Give the device `mac` (MAC address). Optional `site`.")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mac": {"type": "string", "description": "the pending device's MAC address"},
        "site": {"type": "string", "description": "site name or id (optional)"},
    },
    "required": ["mac"],
    "additionalProperties": False,
}


def run(ctx, mac: str, site: str = "", **_: Any):
    m = (mac or "").strip()
    if not re.match(r"^[0-9A-Fa-f:.-]{12,17}$", m):
        return {"ok": False, "error": "give a valid MAC address"}
    client = ctx.client("unifi")
    sid, err = _u.resolve_site(client, site)
    if err:
        return {"ok": False, "error": err}
    r = client.write("POST", f"/v1/sites/{sid}/devices", {"macAddress": m})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "mac": m, "result": r, "note": "adoption submitted"}
