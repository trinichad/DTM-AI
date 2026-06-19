"""Cylance console users — list (D-82)."""
from __future__ import annotations

from typing import Any

NAME = "cylance_list_users"
DESCRIPTION = ("List the Cylance Console users for this client (id, email, name, role) — who has "
               "access to the Cylance console.")
SOURCE = "cylance"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
_FIELDS = ("id", "email", "first_name", "last_name", "roles", "default_zone_role")


def run(ctx, **_: Any):
    out = []
    for u in ctx.client("cylance").get_paginated("/users/v2"):
        out.append({k: u.get(k) for k in _FIELDS if k in u} or u)
    return out
