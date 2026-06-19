"""List UniFi devices pending adoption (D-84)."""
from __future__ import annotations

from typing import Any

from . import _unifi_common as _u

NAME = "unifi_pending_devices"
DESCRIPTION = ("List UniFi devices that are on the network but NOT yet adopted (pending adoption) "
               "— model, MAC, IP. Adopt one with unifi_adopt_device.")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
_FIELDS = ("id", "model", "macAddress", "ipAddress", "state")


def run(ctx, **_: Any):
    out = []
    for d in ctx.client("unifi").get_paginated("/v1/pending-devices"):
        out.append(_u.slim(d, _FIELDS) if isinstance(d, dict) else d)
    return out
