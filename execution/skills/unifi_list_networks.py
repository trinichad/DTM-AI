"""List UniFi networks/VLANs (D-84)."""
from __future__ import annotations

from typing import Any

from . import _unifi_common as _u

NAME = "unifi_list_networks"
DESCRIPTION = ("List the UniFi networks (LANs/VLANs) on a site — name, VLAN id, subnet, purpose. "
               "Optional `site`.")
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
_FIELDS = ("id", "name", "vlanId", "purpose", "subnet", "domainName", "enabled")


def run(ctx, site: str = "", **_: Any):
    client = ctx.client("unifi")
    sid, err = _u.resolve_site(client, site)
    if err:
        return {"ok": False, "error": err}
    out = []
    for n in client.get_paginated(f"/v1/sites/{sid}/networks"):
        out.append(_u.slim(n, _FIELDS))
    return out
