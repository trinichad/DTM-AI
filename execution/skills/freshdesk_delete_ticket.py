"""Delete a Freshdesk ticket (D-83) — destructive (restorable with freshdesk_restore_ticket)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_delete_ticket"
DESCRIPTION = ("Delete a Freshdesk ticket by `ticket_id` (moves it to trash — it can be brought "
               "back with freshdesk_restore_ticket). Destructive, so it always needs a per-action "
               "approval. Pass `ticket_id` for one or `ticket_ids` (a list) to delete MANY in ONE "
               "call — do NOT call this tool once per ticket.")
SOURCE = "freshdesk"
GROUP = "freshdesk_tickets"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticket_id": {"type": "integer", "description": "the Freshdesk ticket id to delete"},
        "ticket_ids": {"type": "array", "items": {"type": "integer"},
                       "description": "delete MANY tickets in ONE call — a list of ticket ids; "
                                      "results come back together. Use this instead of calling the "
                                      "tool once per ticket."},
    },
    "additionalProperties": False,
}


def run(ctx, ticket_id: Any = None, ticket_ids: Any = None, **_: Any):
    wanted = [int(x) for x in (ticket_ids or [])]
    if wanted:                                         # batch (D-110) — one call, many tickets
        results = [_one(ctx, t) for t in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "tickets_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, ticket_id)


def _one(ctx, ticket_id: int) -> dict:
    tid = int(ticket_id)
    r = ctx.client("freshdesk").write_destructive("DELETE", f"/tickets/{tid}", None)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "ticket_id": tid, "error": r["error"]}
    return {"ok": True, "ticket_id": tid, "note": "ticket deleted (restorable)"}
