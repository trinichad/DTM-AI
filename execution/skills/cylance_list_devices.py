"""List Cylance-protected devices (trimmed payload)."""
from __future__ import annotations

from typing import Any

NAME = "cylance_list_devices"
DESCRIPTION = ("List endpoints protected by Cylance for this client. "
               "Returns id, name, state, agent_version, os_version, last_logged_in_user.")
SOURCE = "cylance"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}

_FIELDS = ("id", "name", "state", "agent_version", "os_version", "last_logged_in_user", "policy")


def _slim(d: dict) -> dict:
    out = {}
    for k in _FIELDS:
        v = d.get(k)
        if v is None:  # tolerate camelCase variants
            v = d.get("".join(p.title() if i else p for i, p in enumerate(k.split("_"))))
        out[k] = v
    return out


def run(ctx, **_: Any):
    # Dedup by device id: Cylance pagination drifts (the live device list shifts while we page),
    # so records near page boundaries get returned on two pages. Counting raw yields a bogus
    # over-count (e.g. 1800 = 9 full pages, vs 1708 real). Unique id is the authoritative count.
    seen: set = set()
    out: list[dict] = []
    for d in ctx.client("cylance").get_paginated("/devices/v2"):
        slim = _slim(d)
        key = slim.get("id")
        if key is not None and key in seen:
            continue
        if key is not None:
            seen.add(key)
        out.append(slim)
    return out
