"""List the members of a Google Workspace group via the Admin SDK Directory API (D-118) — read-only."""
from __future__ import annotations

from typing import Any

NAME = "gws_group_members"
DESCRIPTION = ("List the members of a Google Workspace group (email, role OWNER/MANAGER/MEMBER, "
               "type USER/GROUP, status). Pass `group` as the group's email or id. Returns the whole "
               "membership in ONE call.")
SOURCE = "gws"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "group": {"type": "string", "description": "the group's email address or id"},
    },
    "required": ["group"],
    "additionalProperties": False,
}


def _slim(m: dict) -> dict:
    return {"email": m.get("email"), "role": m.get("role"), "type": m.get("type"),
            "status": m.get("status")}


def run(ctx, group: str = "", **_: Any):
    from ._gws_common import scan
    from execution.clients.scopes import scoped_read
    g = (group or "").strip()
    if not g:
        return {"ok": False, "error": "group (email or id) is required"}
    path = f"/admin/directory/v1/groups/{g}/members"
    members, err, trunc = scan(lambda p: scoped_read(ctx, "gws", path, p),
                               {"maxResults": 200}, "members")
    if err:
        return err
    out: dict[str, Any] = {"group": g, "count": len(members),
                           "members": [_slim(m) for m in members]}
    if trunc:
        out["note"] = "hit the page cap — the group has more members than shown"
    return out
