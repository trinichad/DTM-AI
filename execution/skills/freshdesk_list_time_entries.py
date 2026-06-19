"""List time entries on a Freshdesk ticket (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_list_time_entries"
DESCRIPTION = ("List the time entries logged on a Freshdesk ticket (`ticket_id`) — who logged "
               "time, how long, whether it's billable, and the note. For billing reconciliation.")
SOURCE = "freshdesk"
GROUP = "freshdesk_time"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"ticket_id": {"type": "integer", "description": "the Freshdesk ticket id"}},
    "required": ["ticket_id"],
    "additionalProperties": False,
}
_FIELDS = ("id", "agent_id", "time_spent", "billable", "note", "executed_at", "created_at")


def run(ctx, ticket_id: int, **_: Any):
    tid = int(ticket_id)
    out = []
    for e in ctx.client("freshdesk").get_paginated(f"/tickets/{tid}/time_entries"):
        out.append(_f.slim(e, _FIELDS))
    return out
