"""Search Freshdesk contacts with the filter query language (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_search_contacts"
DESCRIPTION = ("Search Freshdesk contacts with the filter query language. Pass `query` without the "
               "outer quotes, e.g. \"name:'John'\" or \"company_id:5 AND active:true\". Supports "
               "name, email, phone, mobile, company_id, active, tag, created_at, updated_at.")
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "the filter query (no surrounding quotes)"}},
    "required": ["query"],
    "additionalProperties": False,
}
_FIELDS = ("id", "name", "email", "phone", "mobile", "company_id", "active")


def run(ctx, query: str, **_: Any):
    q = (query or "").strip()
    if not q or len(q) > 512:
        return {"ok": False, "error": "give a valid search query"}
    out = []
    for c in ctx.client("freshdesk").get_paginated("/search/contacts", {"query": f'"{q}"'},
                                                   per_page=30, max_pages=10):
        out.append(_f.slim(c, _FIELDS))
    return out
