"""Update a Freshdesk company (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_update_company"
DESCRIPTION = ("Update a Freshdesk company by `company_id` — name, domains, note, industry. Only "
               "the fields you pass change.")
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_id": {"type": "integer", "description": "the Freshdesk company id"},
        "name": {"type": "string"},
        "domains": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "string"},
        "industry": {"type": "string"},
    },
    "required": ["company_id"],
    "additionalProperties": False,
}


def run(ctx, company_id: int, name: str = "", domains: Any = None, note: str = "",
        industry: str = "", **_: Any):
    body: dict[str, Any] = {}
    if (name or "").strip():
        body["name"] = name.strip()[:255]
    if isinstance(domains, list):
        body["domains"] = [str(d).strip() for d in domains if str(d or "").strip()]
    if (note or "").strip():
        body["note"] = note.strip()
    if (industry or "").strip():
        body["industry"] = industry.strip()
    if not body:
        return {"ok": False, "error": "give at least one field to change"}
    r = ctx.client("freshdesk").write("PUT", f"/companies/{int(company_id)}", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "company": r, "note": "company updated"}
