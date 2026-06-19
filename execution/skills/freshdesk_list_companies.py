"""List Freshdesk companies (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_list_companies"
DESCRIPTION = ("List Freshdesk companies (the organizations contacts belong to). Returns id, name, "
               "domains, and notes.")
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name_contains": {"type": "string", "description": "case-insensitive name substring filter"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 300},
    },
    "additionalProperties": False,
}
_FIELDS = ("id", "name", "domains", "note", "created_at", "updated_at")


def run(ctx, name_contains: str = "", limit: Any = None, **_: Any):
    needle = (name_contains or "").strip().lower()
    cap = 200 if limit is None else max(1, min(300, int(limit)))
    out = []
    for c in ctx.client("freshdesk").get_paginated("/companies"):
        if needle and needle not in str(c.get("name", "")).lower():
            continue
        out.append(_f.slim(c, _FIELDS))
        if len(out) >= cap:
            break
    return out
