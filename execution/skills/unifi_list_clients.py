"""List connected UniFi clients (D-84)."""
from __future__ import annotations

from typing import Any

from . import _unifi_common as _u

NAME = "unifi_list_clients"
DESCRIPTION = ("List the clients (devices) connected to the UniFi network — name, IP, MAC, "
               "connection type, and uptime. Optional `site` (name or id; defaults to the only/"
               "Default site) and `name_contains` filter.")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "site": {"type": "string", "description": "site name or id (optional)"},
        "name_contains": {"type": "string", "description": "case-insensitive name/hostname filter"},
    },
    "additionalProperties": False,
}
_FIELDS = ("id", "name", "hostname", "ipAddress", "macAddress", "type", "connectedAt", "uplinkDeviceId")


def run(ctx, site: str = "", name_contains: str = "", **_: Any):
    client = ctx.client("unifi")
    sid, err = _u.resolve_site(client, site)
    if err:
        return {"ok": False, "error": err}
    needle = (name_contains or "").strip().lower()
    out = []
    for c in client.get_paginated(f"/v1/sites/{sid}/clients"):
        if needle and needle not in (str(c.get("name", "")) + str(c.get("hostname", ""))).lower():
            continue
        out.append(_u.slim(c, _FIELDS))
    return out
