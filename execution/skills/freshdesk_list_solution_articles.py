"""List Freshdesk solution articles in a folder (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_list_solution_articles"
DESCRIPTION = ("List the knowledge-base articles inside a folder. Give the `folder_id`. Returns "
               "id, title, status (1=draft, 2=published), and view/hit counts.")
SOURCE = "freshdesk"
GROUP = "freshdesk_kb"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"folder_id": {"type": "integer", "description": "the KB folder id"}},
    "required": ["folder_id"],
    "additionalProperties": False,
}
_FIELDS = ("id", "title", "status", "hits", "thumbs_up", "thumbs_down", "updated_at")


def run(ctx, folder_id: int, **_: Any):
    fid = int(folder_id)
    out = []
    for a in ctx.client("freshdesk").get_paginated(f"/solutions/folders/{fid}/articles"):
        out.append(_f.slim(a, _FIELDS))
    return out
