"""List / SEARCH Google Workspace groups via the Admin SDK Directory API (D-118) — read-only."""
from __future__ import annotations

from typing import Any

NAME = "gws_list_groups"
DESCRIPTION = ("List or search Google Workspace groups (email, name, description, member count). "
               "Use `search` to pass a Directory groups query (e.g. \"email:sales*\", "
               "\"name:'All Staff'\") — returns MANY groups in ONE call; do not call once per group. "
               "Scoped to the selected client; on 'All clients' (*) it aggregates across every "
               "signed-in Google Workspace client, tagging each group with its `tenant`.")
SOURCE = "gws"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True

_PATH = "/admin/directory/v1/groups"
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "search": {"type": "string",
                   "description": "a Directory groups query, e.g. \"email:sales*\" or "
                                  "\"name:'All Staff'\" — returns the whole matching set in one call"},
        "top": {"type": "integer", "description": "max groups to return (1–200, default 200)"},
    },
    "additionalProperties": False,
}


def _slim(g: dict) -> dict:
    out: dict[str, Any] = {"email": g.get("email"), "name": g.get("name"),
                           "members": g.get("directMembersCount")}
    for k in ("description", "tenant"):
        if g.get(k):
            out[k] = g[k]
    return out


def run(ctx, search: str = "", top: int = 200, **_: Any):
    from ._gws_common import read_list
    try:
        top = max(1, min(int(top), 200))
    except (TypeError, ValueError):
        top = 200
    base: dict[str, Any] = {"customer": "my_customer", "maxResults": top}
    if (search or "").strip():
        base["query"] = search.strip()
    return read_list(ctx, _PATH, base, "groups", _slim, out_key="groups")
