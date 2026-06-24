"""List Freshdesk satisfaction (CSAT) ratings (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_satisfaction_ratings"
DESCRIPTION = ("List Freshdesk customer satisfaction (CSAT) ratings — the survey responses "
               "customers left on resolved tickets, with the score and any feedback. Pass a "
               "`ticket_id` to scope to one ticket, or `ticket_ids` (a list) to scope to MANY in "
               "ONE call — do NOT call this tool once per ticket.")
SOURCE = "freshdesk"
GROUP = "freshdesk_admin"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticket_id": {"type": "integer", "description": "scope to one ticket (optional)"},
        "ticket_ids": {"type": "array", "items": {"type": "integer"},
                       "description": "scope to MANY tickets in ONE call — a list of ticket ids; "
                                      "results come back together. Use this instead of calling the "
                                      "tool once per ticket."},
    },
    "additionalProperties": False,
}
_FIELDS = ("id", "survey_id", "ticket_id", "agent_id", "group_id", "ratings", "feedback",
           "created_at")


def run(ctx, ticket_id: Any = None, ticket_ids: Any = None, **_: Any):
    wanted = [int(x) for x in (ticket_ids or [])]
    if wanted:                                         # batch (D-110) — one call, many tickets
        results = [_one(ctx, t) for t in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "tickets_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one_raw(ctx, ticket_id)


def _one_raw(ctx, ticket_id: Any = None) -> list:
    path = (f"/tickets/{int(ticket_id)}/satisfaction_ratings" if ticket_id is not None
            else "/satisfaction_ratings")
    out = []
    for r in ctx.client("freshdesk").get_paginated(path):
        out.append(_f.slim(r, _FIELDS))
    return out


def _one(ctx, ticket_id: int) -> dict:
    return {"ok": True, "ticket_id": int(ticket_id), "ratings": _one_raw(ctx, ticket_id)}
