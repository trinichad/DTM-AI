"""Create a Freshdesk ticket (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_create_ticket"
DESCRIPTION = ("Create a Freshdesk ticket. Give the `subject`, the `description` (the body, HTML "
               "allowed), and the requester's `email` (or a `requester_id`). Optional: priority "
               "(low/medium/high/urgent, default medium), status (open/pending/resolved/closed, "
               "default open), type, group_id, agent_id (responder), tags, cc_emails. Returns the "
               "new ticket.")
SOURCE = "freshdesk"
GROUP = "freshdesk_tickets"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "the ticket subject"},
        "description": {"type": "string", "description": "the ticket body (HTML allowed)"},
        "email": {"type": "string", "description": "the requester's email"},
        "requester_id": {"type": "integer", "description": "the requester contact id (instead of email)"},
        "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
        "status": {"type": "string", "enum": ["open", "pending", "resolved", "closed"]},
        "type": {"type": "string", "description": "ticket type, e.g. Incident, Service Request"},
        "group_id": {"type": "integer"},
        "agent_id": {"type": "integer", "description": "responder/agent to assign"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "cc_emails": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["subject", "description"],
    "additionalProperties": False,
}


def run(ctx, subject: str, description: str, email: str = "", requester_id: Any = None,
        priority: str = "medium", status: str = "open", type: str = "", group_id: Any = None,
        agent_id: Any = None, tags: Any = None, cc_emails: Any = None, **_: Any):
    subj = (subject or "").strip()
    desc = (description or "").strip()
    if not subj or not desc:
        return {"ok": False, "error": "give a subject and a description"}
    if not (email or "").strip() and requester_id is None:
        return {"ok": False, "error": "give the requester's email or a requester_id"}
    body: dict[str, Any] = {
        "subject": subj[:255], "description": desc,
        "priority": _f.priority_id(priority) or 2,
        "status": _f.status_id(status) or 2,
        "source": 2,                                 # Portal
    }
    if (email or "").strip():
        body["email"] = email.strip()
    else:
        body["requester_id"] = int(requester_id)
    if (type or "").strip():
        body["type"] = type.strip()
    if group_id is not None:
        body["group_id"] = int(group_id)
    if agent_id is not None:
        body["responder_id"] = int(agent_id)
    if isinstance(tags, list) and tags:
        body["tags"] = [str(t).strip() for t in tags if str(t or "").strip()]
    if isinstance(cc_emails, list) and cc_emails:
        body["cc_emails"] = [str(e).strip() for e in cc_emails if str(e or "").strip()]
    r = ctx.client("freshdesk").write("POST", "/tickets", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "ticket": _f.slim_ticket(r) if isinstance(r, dict) else r,
            "note": "ticket created"}
