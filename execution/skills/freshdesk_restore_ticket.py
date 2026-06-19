"""Restore a deleted Freshdesk ticket (D-83). Opposite of freshdesk_delete_ticket."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_restore_ticket"
DESCRIPTION = ("Restore a previously-deleted Freshdesk ticket by `ticket_id` (un-deletes it)."
               )
SOURCE = "freshdesk"
GROUP = "freshdesk_tickets"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticket_id": {"type": "integer", "description": "the Freshdesk ticket id to restore"},
    },
    "required": ["ticket_id"],
    "additionalProperties": False,
}


def run(ctx, ticket_id: int, **_: Any):
    tid = int(ticket_id)
    r = ctx.client("freshdesk").write("PUT", f"/tickets/{tid}/restore", None)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "ticket_id": tid, "note": "ticket restored"}
