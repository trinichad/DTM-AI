"""List adopted UniFi devices (APs, switches, gateways) (D-84)."""
from __future__ import annotations

from typing import Any

from . import _unifi_common as _u

NAME = "unifi_list_devices"
DESCRIPTION = ("List the adopted UniFi devices (access points, switches, gateways) on a site — "
               "name, model, IP, MAC, state, and firmware. Optional `site` and `name_contains`.")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "site": {"type": "string", "description": "site name or id (optional)"},
        "name_contains": {"type": "string", "description": "case-insensitive name filter"},
    },
    "additionalProperties": False,
}
_FIELDS = ("id", "name", "model", "ipAddress", "macAddress", "state", "firmwareVersion",
           "supported", "features")


def run(ctx, site: str = "", name_contains: str = "", **_: Any):
    client = ctx.client("unifi")
    sid, err = _u.resolve_site(client, site)
    if err:
        return {"ok": False, "error": err}
    needle = (name_contains or "").strip().lower()
    out = []
    for d in client.get_paginated(f"/v1/sites/{sid}/devices"):
        if needle and needle not in str(d.get("name", "")).lower():
            continue
        out.append(_u.slim(d, _FIELDS))
    return out
