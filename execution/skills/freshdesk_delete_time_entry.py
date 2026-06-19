"""Delete a time entry from a Freshdesk ticket (D-83) — destructive."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_delete_time_entry"
DESCRIPTION = ("Delete a logged time entry from a Freshdesk ticket. Give the `ticket_id` and the "
               "`time_entry_id`. Destructive, so it always needs a per-action approval.")
SOURCE = "freshdesk"
GROUP = "freshdesk_time"
CATEGORY = "destructive"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticket_id": {"type": "integer", "description": "the Freshdesk ticket id"},
        "time_entry_id": {"type": "integer", "description": "the time entry id to delete"},
    },
    "required": ["ticket_id", "time_entry_id"],
    "additionalProperties": False,
}


def run(ctx, ticket_id: int, time_entry_id: int, **_: Any):
    tid, eid = int(ticket_id), int(time_entry_id)
    r = ctx.client("freshdesk").write_destructive("DELETE", f"/tickets/{tid}/time_entries/{eid}", None)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "ticket_id": tid, "time_entry_id": eid, "note": "time entry deleted"}
