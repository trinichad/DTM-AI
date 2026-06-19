"""List Freshdesk contacts (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_list_contacts"
DESCRIPTION = ("List Freshdesk contacts (end users). Optional filters: company_id, email, "
               "updated_since. Use freshdesk_search_contacts for name/phone lookups.")
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_id": {"type": "integer"},
        "email": {"type": "string"},
        "updated_since": {"type": "string", "description": "ISO-8601 date"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 300},
    },
    "additionalProperties": False,
}
_FIELDS = ("id", "name", "email", "phone", "mobile", "company_id", "active", "created_at")


def run(ctx, company_id: Any = None, email: str = "", updated_since: str = "", limit: Any = None,
        **_: Any):
    params: dict[str, Any] = {}
    if company_id is not None:
        params["company_id"] = int(company_id)
    if (email or "").strip():
        params["email"] = email.strip()
    if (updated_since or "").strip():
        params["_updated_since"] = updated_since.strip()
    cap = 100 if limit is None else max(1, min(300, int(limit)))
    out = []
    for c in ctx.client("freshdesk").get_paginated("/contacts", params):
        out.append(_f.slim(c, _FIELDS))
        if len(out) >= cap:
            break
    return out
