"""List UniFi hotspot (guest WiFi) vouchers (D-84)."""
from __future__ import annotations

from typing import Any

from . import _unifi_common as _u

NAME = "unifi_list_vouchers"
DESCRIPTION = ("List the UniFi hotspot vouchers (guest WiFi access codes) on a site — code, "
               "duration, data/speed limits, and usage. Optional `site`. Create new ones with "
               "unifi_create_voucher.")
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
_FIELDS = ("id", "code", "name", "timeLimitMinutes", "authorizedGuestLimit", "authorizedGuestCount",
           "dataUsageLimitMBytes", "expired", "createdAt")


def run(ctx, site: str = "", **_: Any):
    client = ctx.client("unifi")
    sid, err = _u.resolve_site(client, site)
    if err:
        return {"ok": False, "error": err}
    out = []
    for v in client.get_paginated(f"/v1/sites/{sid}/hotspot/vouchers"):
        out.append(_u.slim(v, _FIELDS))
    return out
