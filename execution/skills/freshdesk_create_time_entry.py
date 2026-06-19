"""Log a time entry on a Freshdesk ticket (D-83)."""
from __future__ import annotations

import re
from typing import Any

NAME = "freshdesk_create_time_entry"
DESCRIPTION = ("Log time on a Freshdesk ticket (`ticket_id`). Give `time_spent` as 'HH:MM' (e.g. "
               "'01:30' for 90 minutes). Optional: billable (default true), note, agent_id (who "
               "did the work — defaults to the API user).")
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
        "time_spent": {"type": "string", "description": "duration as HH:MM, e.g. 01:30"},
        "billable": {"type": "boolean", "description": "is it billable (default true)"},
        "note": {"type": "string"},
        "agent_id": {"type": "integer", "description": "the agent who did the work (optional)"},
    },
    "required": ["ticket_id", "time_spent"],
    "additionalProperties": False,
}


def run(ctx, ticket_id: int, time_spent: str, billable: bool = True, note: str = "",
        agent_id: Any = None, **_: Any):
    tid = int(ticket_id)
    ts = (time_spent or "").strip()
    if not re.match(r"^\d{1,2}:[0-5]\d$", ts):
        return {"ok": False, "error": "time_spent must be HH:MM, e.g. 01:30"}
    body: dict[str, Any] = {"time_spent": ts, "billable": bool(billable)}
    if (note or "").strip():
        body["note"] = note.strip()
    if agent_id is not None:
        body["agent_id"] = int(agent_id)
    r = ctx.client("freshdesk").write("POST", f"/tickets/{tid}/time_entries", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "ticket_id": tid, "time_entry": r, "note": "time logged"}
