"""Delete a Freshdesk ticket (D-83) — destructive (restorable with freshdesk_restore_ticket)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_delete_ticket"
DESCRIPTION = ("Delete a Freshdesk ticket by `ticket_id` (moves it to trash — it can be brought "
               "back with freshdesk_restore_ticket). Destructive, so it always needs a per-action "
               "approval.")
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
    },
    "required": ["ticket_id"],
    "additionalProperties": False,
}


def run(ctx, ticket_id: int, **_: Any):
    tid = int(ticket_id)
    r = ctx.client("freshdesk").write_destructive("DELETE", f"/tickets/{tid}", None)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "ticket_id": tid, "note": "ticket deleted (restorable)"}
