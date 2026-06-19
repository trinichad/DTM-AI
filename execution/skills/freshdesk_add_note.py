"""Add a note to a Freshdesk ticket — private by default (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_add_note"
DESCRIPTION = ("Add a note to a Freshdesk ticket (`ticket_id`). Notes are PRIVATE (internal, the "
               "customer does NOT see them) by default — set private=false to make it public. "
               "Give the `body` (HTML allowed). Optional notify_emails to alert agents.")
SOURCE = "freshdesk"
GROUP = "freshdesk_tickets"
CATEGORY = "write"
RISK_LEVEL = "low"          # private internal note by default
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticket_id": {"type": "integer", "description": "the Freshdesk ticket id"},
        "body": {"type": "string", "description": "the note text (HTML allowed)"},
        "private": {"type": "boolean", "description": "private/internal (default true)"},
        "notify_emails": {"type": "array", "items": {"type": "string"},
                          "description": "agent emails to notify (optional)"},
    },
    "required": ["ticket_id", "body"],
    "additionalProperties": False,
}


def run(ctx, ticket_id: int, body: str, private: bool = True, notify_emails: Any = None, **_: Any):
    tid = int(ticket_id)
    text = (body or "").strip()
    if not text:
        return {"ok": False, "error": "give the note body"}
    payload: dict[str, Any] = {"body": text, "private": bool(private)}
    if isinstance(notify_emails, list) and notify_emails:
        payload["notify_emails"] = [str(e).strip() for e in notify_emails if str(e or "").strip()]
    r = ctx.client("freshdesk").write("POST", f"/tickets/{tid}/notes", payload)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "ticket_id": tid, "private": bool(private), "note": "note added"}
