"""List Freshdesk solution (knowledge base) categories (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_list_solution_categories"
DESCRIPTION = ("List the Freshdesk knowledge-base categories (the top level of the help center). "
               "Use the category id with freshdesk_list_solution_folders.")
SOURCE = "freshdesk"
GROUP = "freshdesk_kb"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
_FIELDS = ("id", "name", "description", "visible_in_portals")


def run(ctx, **_: Any):
    out = []
    for c in ctx.client("freshdesk").get_paginated("/solutions/categories"):
        out.append(_f.slim(c, _FIELDS))
    return out
