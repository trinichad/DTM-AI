"""List UniFi sites on the console (D-84)."""
from __future__ import annotations

from typing import Any

from . import _unifi_common as _u

NAME = "unifi_list_sites"
DESCRIPTION = ("List the UniFi sites on the console (id + name). Most consoles have one site; the "
               "site id is what the other UniFi tools use (they default to it automatically).")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


def run(ctx, **_: Any):
    return _u.sites(ctx.client("unifi"))
