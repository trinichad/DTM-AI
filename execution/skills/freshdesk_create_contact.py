"""Create a Freshdesk contact (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_create_contact"
DESCRIPTION = ("Create a Freshdesk contact (end user). Give the `name` and at least one of email, "
               "phone, or mobile. Optional: company_id, job_title, address, description.")
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "the contact's full name"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "mobile": {"type": "string"},
        "company_id": {"type": "integer"},
        "job_title": {"type": "string"},
        "address": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["name"],
    "additionalProperties": False,
}


def run(ctx, name: str, email: str = "", phone: str = "", mobile: str = "", company_id: Any = None,
        job_title: str = "", address: str = "", description: str = "", **_: Any):
    nm = (name or "").strip()
    if not nm:
        return {"ok": False, "error": "give the contact's name"}
    if not any(((email or "").strip(), (phone or "").strip(), (mobile or "").strip())):
        return {"ok": False, "error": "give at least one of email, phone, or mobile"}
    body: dict[str, Any] = {"name": nm[:255]}
    for key, val in (("email", email), ("phone", phone), ("mobile", mobile),
                     ("job_title", job_title), ("address", address), ("description", description)):
        if (val or "").strip():
            body[key] = val.strip()
    if company_id is not None:
        body["company_id"] = int(company_id)
    r = ctx.client("freshdesk").write("POST", "/contacts", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "contact": r, "note": "contact created"}
