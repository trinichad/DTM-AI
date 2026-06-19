"""List UniFi WiFi networks (SSIDs) (D-84)."""
from __future__ import annotations

from typing import Any

from . import _unifi_common as _u

NAME = "unifi_list_wifi"
DESCRIPTION = ("List the UniFi WiFi broadcasts (SSIDs) on a site — name, enabled state, security, "
               "and the network/VLAN they map to. Optional `site`.")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"site": {"type": "string", "description": "site name or id (optional)"}},
    "additionalProperties": False,
}
_FIELDS = ("id", "name", "enabled", "securityProtocol", "networkId", "hideSsid", "band")


def run(ctx, site: str = "", **_: Any):
    client = ctx.client("unifi")
    sid, err = _u.resolve_site(client, site)
    if err:
        return {"ok": False, "error": err}
    out = []
    for w in client.get_paginated(f"/v1/sites/{sid}/wifi/broadcasts"):
        out.append(_u.slim(w, _FIELDS))
    return out
