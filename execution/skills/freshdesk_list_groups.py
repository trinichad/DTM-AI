"""List Freshdesk groups (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_list_groups"
DESCRIPTION = ("List the Freshdesk groups (teams tickets are routed to) — id, name, description, "
               "and agent ids.")
SOURCE = "freshdesk"
GROUP = "freshdesk_team"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
_FIELDS = ("id", "name", "description", "agent_ids", "business_hour_id", "escalate_to")


def run(ctx, **_: Any):
    out = []
    for g in ctx.client("freshdesk").get_paginated("/groups"):
        out.append(_f.slim(g, _FIELDS))
    return out
