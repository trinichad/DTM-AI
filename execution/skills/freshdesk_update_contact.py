"""Update a Freshdesk contact (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_update_contact"
DESCRIPTION = ("Update a Freshdesk contact by `contact_id` — name, email, phone, mobile, "
               "company_id, job_title, address. Only the fields you pass change.")
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "contact_id": {"type": "integer", "description": "the Freshdesk contact id"},
        "name": {"type": "string"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "mobile": {"type": "string"},
        "company_id": {"type": "integer"},
        "job_title": {"type": "string"},
        "address": {"type": "string"},
    },
    "required": ["contact_id"],
    "additionalProperties": False,
}


def run(ctx, contact_id: int, name: str = "", email: str = "", phone: str = "", mobile: str = "",
        company_id: Any = None, job_title: str = "", address: str = "", **_: Any):
    body: dict[str, Any] = {}
    for key, val in (("name", name), ("email", email), ("phone", phone), ("mobile", mobile),
                     ("job_title", job_title), ("address", address)):
        if (val or "").strip():
            body[key] = val.strip()
    if company_id is not None:
        body["company_id"] = int(company_id)
    if not body:
        return {"ok": False, "error": "give at least one field to change"}
    r = ctx.client("freshdesk").write("PUT", f"/contacts/{int(contact_id)}", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "contact": r, "note": "contact updated"}
