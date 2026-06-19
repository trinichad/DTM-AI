"""Get one Freshdesk ticket's full detail (D-83)."""
from __future__ import annotations

import re
from typing import Any

NAME = "freshdesk_get_ticket"
DESCRIPTION = ("Get the full detail of one Freshdesk ticket by `ticket_id` — subject, description, "
               "requester, status/priority, custom fields, tags, stats. Use "
               "freshdesk_ticket_conversations for the message thread.")
SOURCE = "freshdesk"
GROUP = "freshdesk_tickets"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticket_id": {"type": "integer", "description": "the Freshdesk ticket id"},
        "include": {"type": "string",
                    "description": "extra embeds, comma-separated: conversations, requester, "
                                   "company, stats (optional)"},
    },
    "required": ["ticket_id"],
    "additionalProperties": False,
}


def run(ctx, ticket_id: int, include: str = "", **_: Any):
    tid = int(ticket_id)
    params = {}
    if (include or "").strip():
        allowed = {"conversations", "requester", "company", "stats"}
        picks = [p.strip() for p in include.split(",") if p.strip() in allowed]
        if picks:
            params["include"] = ",".join(picks)
    return ctx.client("freshdesk").get(f"/tickets/{tid}", params or None)
