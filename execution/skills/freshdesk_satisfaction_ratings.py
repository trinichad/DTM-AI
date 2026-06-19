"""List Freshdesk satisfaction (CSAT) ratings (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_satisfaction_ratings"
DESCRIPTION = ("List Freshdesk customer satisfaction (CSAT) ratings — the survey responses "
               "customers left on resolved tickets, with the score and any feedback. Pass a "
               "`ticket_id` to scope to one ticket.")
SOURCE = "freshdesk"
GROUP = "freshdesk_admin"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticket_id": {"type": "integer", "description": "scope to one ticket (optional)"},
    },
    "additionalProperties": False,
}
_FIELDS = ("id", "survey_id", "ticket_id", "agent_id", "group_id", "ratings", "feedback",
           "created_at")


def run(ctx, ticket_id: Any = None, **_: Any):
    path = (f"/tickets/{int(ticket_id)}/satisfaction_ratings" if ticket_id is not None
            else "/satisfaction_ratings")
    out = []
    for r in ctx.client("freshdesk").get_paginated(path):
        out.append(_f.slim(r, _FIELDS))
    return out
