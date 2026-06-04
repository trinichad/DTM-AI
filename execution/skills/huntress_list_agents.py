"""List Huntress agents (trimmed payload)."""
from __future__ import annotations

from typing import Any

NAME = "huntress_list_agents"
DESCRIPTION = ("List Huntress agents for this client. Returns id, hostname, platform, version "
               "(Huntress agent version), last_seen_at, organization_id. There can be THOUSANDS of "
               "agents — pass name_contains (case-insensitive hostname substring, e.g. 'rho') to get a "
               "complete focused result you can cross-reference, instead of a truncated list.")
SOURCE = "huntress"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name_contains": {"type": "string",
                          "description": "case-insensitive substring filter on the agent hostname"}
    },
    "additionalProperties": False,
}

_FIELDS = ("id", "hostname", "platform", "version", "last_seen_at", "last_callback_at",
           "organization_id", "account_id")


def run(ctx, name_contains: str = "", **_: Any):
    needle = (name_contains or "").strip().lower()
    out = []
    for a in ctx.client("huntress").get_paginated("/agents"):
        if needle and needle not in str(a.get("hostname", "")).lower():
            continue
        out.append({k: a.get(k) for k in _FIELDS})
    return out
