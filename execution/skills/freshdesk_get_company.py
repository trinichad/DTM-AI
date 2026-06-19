"""Get one Freshdesk company's detail (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_get_company"
DESCRIPTION = "Get the full detail of one Freshdesk company by `company_id`."
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"company_id": {"type": "integer", "description": "the Freshdesk company id"}},
    "required": ["company_id"],
    "additionalProperties": False,
}


def run(ctx, company_id: int, **_: Any):
    return ctx.client("freshdesk").get(f"/companies/{int(company_id)}")
