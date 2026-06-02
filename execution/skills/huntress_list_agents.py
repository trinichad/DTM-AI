"""List Huntress agents (trimmed payload)."""
from __future__ import annotations

from typing import Any

NAME = "huntress_list_agents"
DESCRIPTION = ("List Huntress agents for this client. "
               "Returns id, hostname, platform, version, last_seen_at, organization_id.")
SOURCE = "huntress"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}

_FIELDS = ("id", "hostname", "platform", "version", "last_seen_at", "last_callback_at",
           "organization_id", "account_id")


def run(ctx, **_: Any):
    return [{k: a.get(k) for k in _FIELDS}
            for a in ctx.client("huntress").get_paginated("/agents")]
