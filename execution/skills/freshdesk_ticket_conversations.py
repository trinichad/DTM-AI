"""Get a Freshdesk ticket's conversation thread — replies + notes (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_ticket_conversations"
DESCRIPTION = ("Get the conversation thread on a Freshdesk ticket (`ticket_id`) — every public "
               "reply and private note, oldest to newest, with author, body, and whether it was "
               "private/incoming.")
SOURCE = "freshdesk"
GROUP = "freshdesk_tickets"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticket_id": {"type": "integer", "description": "the Freshdesk ticket id"},
    },
    "required": ["ticket_id"],
    "additionalProperties": False,
}
_FIELDS = ("id", "user_id", "incoming", "private", "body_text", "from_email", "to_emails",
           "created_at")


def run(ctx, ticket_id: int, **_: Any):
    tid = int(ticket_id)
    out = []
    for c in ctx.client("freshdesk").get_paginated(f"/tickets/{tid}/conversations"):
        out.append(_f.slim(c, _FIELDS))
    return out
