"""Get one Freshdesk ticket's full detail (D-83)."""
from __future__ import annotations

import re
from typing import Any

NAME = "freshdesk_get_ticket"
DESCRIPTION = ("Get the full detail of one Freshdesk ticket by `ticket_id` — subject, description, "
               "requester, status/priority, custom fields, tags, stats. Use "
               "freshdesk_ticket_conversations for the message thread. Pass `ticket_id` for one or "
               "`ticket_ids` (a list) to fetch MANY in ONE call — do NOT call this tool once per "
               "ticket.")
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
                       "description": "fetch MANY tickets in ONE call — a list of ticket ids; "
                                      "results come back together. Use this instead of calling the "
                                      "tool once per ticket."},
        "include": {"type": "string",
                    "description": "extra embeds, comma-separated: conversations, requester, "
                                   "company, stats (optional)"},
    },
    "additionalProperties": False,
}


def run(ctx, ticket_id: Any = None, ticket_ids: Any = None, include: str = "", **_: Any):
    wanted = [int(x) for x in (ticket_ids or [])]
    if wanted:                                         # batch (D-110) — one call, many tickets
        results = ctx.map_progress(wanted[:500], lambda t: _one(ctx, t, include))
        return {"ok": any(r.get("ok") for r in results), "tickets_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, ticket_id, include)


def _one(ctx, ticket_id: int, include: str = "") -> dict:
    tid = int(ticket_id)
    params = {}
    if (include or "").strip():
        allowed = {"conversations", "requester", "company", "stats"}
        picks = [p.strip() for p in include.split(",") if p.strip() in allowed]
        if picks:
            params["include"] = ",".join(picks)
    return ctx.client("freshdesk").get(f"/tickets/{tid}", params or None)
