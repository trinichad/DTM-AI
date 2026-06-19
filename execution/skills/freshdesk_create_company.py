"""Create a Freshdesk company (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_create_company"
DESCRIPTION = ("Create a Freshdesk company (an organization contacts belong to). Give the `name`. "
               "Optional: domains (email domains that auto-associate contacts), note, "
               "health_score, industry.")
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "the company name"},
        "domains": {"type": "array", "items": {"type": "string"},
                    "description": "email domains to auto-associate, e.g. ['acme.com']"},
        "note": {"type": "string"},
        "industry": {"type": "string"},
    },
    "required": ["name"],
    "additionalProperties": False,
}


def run(ctx, name: str, domains: Any = None, note: str = "", industry: str = "", **_: Any):
    nm = (name or "").strip()
    if not nm:
        return {"ok": False, "error": "give the company name"}
    body: dict[str, Any] = {"name": nm[:255]}
    if isinstance(domains, list) and domains:
        body["domains"] = [str(d).strip() for d in domains if str(d or "").strip()]
    if (note or "").strip():
        body["note"] = note.strip()
    if (industry or "").strip():
        body["industry"] = industry.strip()
    r = ctx.client("freshdesk").write("POST", "/companies", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "company": r, "note": "company created"}
