"""List Freshdesk solution folders in a category (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_list_solution_folders"
DESCRIPTION = ("List the knowledge-base folders inside a category. Give the `category_id`. Use a "
               "folder id with freshdesk_list_solution_articles or freshdesk_create_solution_article.")
SOURCE = "freshdesk"
GROUP = "freshdesk_kb"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"category_id": {"type": "integer", "description": "the KB category id"}},
    "required": ["category_id"],
    "additionalProperties": False,
}
_FIELDS = ("id", "name", "description", "visibility", "articles_count")


def run(ctx, category_id: int, **_: Any):
    cid = int(category_id)
    out = []
    for f in ctx.client("freshdesk").get_paginated(f"/solutions/categories/{cid}/folders"):
        out.append(_f.slim(f, _FIELDS))
    return out
