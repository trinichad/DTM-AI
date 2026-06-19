"""Get one Freshdesk contact's detail (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_get_contact"
DESCRIPTION = "Get the full detail of one Freshdesk contact by `contact_id`."
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"contact_id": {"type": "integer", "description": "the Freshdesk contact id"}},
    "required": ["contact_id"],
    "additionalProperties": False,
}


def run(ctx, contact_id: int, **_: Any):
    return ctx.client("freshdesk").get(f"/contacts/{int(contact_id)}")
