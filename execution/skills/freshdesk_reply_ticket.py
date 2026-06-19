"""Post a public reply to the customer on a Freshdesk ticket (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_reply_ticket"
DESCRIPTION = ("Send a PUBLIC reply to the customer on a Freshdesk ticket (`ticket_id`) — they "
               "receive it by email. Give the `body` (HTML allowed). Optional cc_emails/bcc_emails. "
               "For an internal-only note use freshdesk_add_note instead.")
SOURCE = "freshdesk"
GROUP = "freshdesk_tickets"
CATEGORY = "write"
RISK_LEVEL = "high"          # goes out to the customer
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticket_id": {"type": "integer", "description": "the Freshdesk ticket id"},
        "body": {"type": "string", "description": "the reply text (HTML allowed)"},
        "cc_emails": {"type": "array", "items": {"type": "string"}},
        "bcc_emails": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ticket_id", "body"],
    "additionalProperties": False,
}


def run(ctx, ticket_id: int, body: str, cc_emails: Any = None, bcc_emails: Any = None, **_: Any):
    tid = int(ticket_id)
    text = (body or "").strip()
    if not text:
        return {"ok": False, "error": "give the reply body"}
    payload: dict[str, Any] = {"body": text}
    if isinstance(cc_emails, list) and cc_emails:
        payload["cc_emails"] = [str(e).strip() for e in cc_emails if str(e or "").strip()]
    if isinstance(bcc_emails, list) and bcc_emails:
        payload["bcc_emails"] = [str(e).strip() for e in bcc_emails if str(e or "").strip()]
    r = ctx.client("freshdesk").write("POST", f"/tickets/{tid}/reply", payload)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "ticket_id": tid, "note": "public reply sent to the customer"}
