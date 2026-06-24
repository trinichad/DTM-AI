"""Get a Freshdesk ticket's conversation thread — replies + notes (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_ticket_conversations"
DESCRIPTION = ("Get the conversation thread on a Freshdesk ticket (`ticket_id`) — every public "
               "reply and private note, oldest to newest, with author, body, and whether it was "
               "private/incoming. Pass `ticket_id` for one or `ticket_ids` (a list) to fetch MANY "
               "in ONE call — do NOT call this tool once per ticket.")
SOURCE = "freshdesk"
GROUP = "freshdesk_tickets"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticket_id": {"type": "integer", "description": "the Freshdesk ticket id"},
        "ticket_ids": {"type": "array", "items": {"type": "integer"},
                       "description": "fetch MANY tickets' threads in ONE call — a list of ticket "
                                      "ids; results come back together. Use this instead of "
                                      "calling the tool once per ticket."},
    },
    "additionalProperties": False,
}
_FIELDS = ("id", "user_id", "incoming", "private", "body_text", "from_email", "to_emails",
           "created_at")


def run(ctx, ticket_id: Any = None, ticket_ids: Any = None, **_: Any):
    wanted = [int(x) for x in (ticket_ids or [])]
    if wanted:                                         # batch (D-110) — one call, many tickets
        results = [_one(ctx, t) for t in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "tickets_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one_raw(ctx, ticket_id)


def _one_raw(ctx, ticket_id: int) -> list:
    tid = int(ticket_id)
    out = []
    for c in ctx.client("freshdesk").get_paginated(f"/tickets/{tid}/conversations"):
        out.append(_f.slim(c, _FIELDS))
    return out


def _one(ctx, ticket_id: int) -> dict:
    return {"ok": True, "ticket_id": int(ticket_id), "conversations": _one_raw(ctx, ticket_id)}
