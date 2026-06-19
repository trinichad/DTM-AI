"""Forward a Freshdesk ticket to other recipients (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_forward_ticket"
DESCRIPTION = ("Forward a Freshdesk ticket (`ticket_id`) to one or more email addresses — e.g. to "
               "loop in a vendor or another team. Give `to_emails` and a `body`. Optional "
               "cc_emails/bcc_emails.")
SOURCE = "freshdesk"
GROUP = "freshdesk_tickets"
CATEGORY = "write"
RISK_LEVEL = "high"          # leaves the helpdesk to external recipients
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticket_id": {"type": "integer", "description": "the Freshdesk ticket id"},
        "to_emails": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "body": {"type": "string", "description": "the forward message (HTML allowed)"},
        "cc_emails": {"type": "array", "items": {"type": "string"}},
        "bcc_emails": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ticket_id", "to_emails", "body"],
    "additionalProperties": False,
}


def run(ctx, ticket_id: int, to_emails: Any, body: str, cc_emails: Any = None,
        bcc_emails: Any = None, **_: Any):
    tid = int(ticket_id)
    tos = [str(e).strip() for e in to_emails if str(e or "").strip()] \
        if isinstance(to_emails, list) else []
    text = (body or "").strip()
    if not tos:
        return {"ok": False, "error": "give at least one to_emails address"}
    if not text:
        return {"ok": False, "error": "give a body for the forward"}
    payload: dict[str, Any] = {"to_emails": tos, "body": text}
    if isinstance(cc_emails, list) and cc_emails:
        payload["cc_emails"] = [str(e).strip() for e in cc_emails if str(e or "").strip()]
    if isinstance(bcc_emails, list) and bcc_emails:
        payload["bcc_emails"] = [str(e).strip() for e in bcc_emails if str(e or "").strip()]
    r = ctx.client("freshdesk").write("POST", f"/tickets/{tid}/forward", payload)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "ticket_id": tid, "to": tos, "note": "ticket forwarded"}
