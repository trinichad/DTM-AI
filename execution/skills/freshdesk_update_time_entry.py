"""Update a time entry on a Freshdesk ticket (D-83)."""
from __future__ import annotations

import re
from typing import Any

NAME = "freshdesk_update_time_entry"
DESCRIPTION = ("Update a logged time entry on a Freshdesk ticket. Give the `ticket_id` and the "
               "`time_entry_id`, then any of: time_spent (HH:MM), billable, note. Only the fields "
               "you pass change.")
SOURCE = "freshdesk"
GROUP = "freshdesk_time"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticket_id": {"type": "integer", "description": "the Freshdesk ticket id"},
        "time_entry_id": {"type": "integer", "description": "the time entry id"},
        "time_spent": {"type": "string", "description": "duration as HH:MM"},
        "billable": {"type": "boolean"},
        "note": {"type": "string"},
    },
    "required": ["ticket_id", "time_entry_id"],
    "additionalProperties": False,
}


def run(ctx, ticket_id: int, time_entry_id: int, time_spent: str = "", billable: Any = None,
        note: str = "", **_: Any):
    tid, eid = int(ticket_id), int(time_entry_id)
    body: dict[str, Any] = {}
    if (time_spent or "").strip():
        ts = time_spent.strip()
        if not re.match(r"^\d{1,2}:[0-5]\d$", ts):
            return {"ok": False, "error": "time_spent must be HH:MM"}
        body["time_spent"] = ts
    if billable is not None:
        body["billable"] = bool(billable)
    if (note or "").strip():
        body["note"] = note.strip()
    if not body:
        return {"ok": False, "error": "give at least one field to change"}
    r = ctx.client("freshdesk").write("PUT", f"/tickets/{tid}/time_entries/{eid}", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "ticket_id": tid, "time_entry_id": eid, "note": "time entry updated"}
