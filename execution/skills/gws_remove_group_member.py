"""Remove a member from a Google Workspace group — Directory API DELETE (D-118)."""
from __future__ import annotations

from typing import Any

NAME = "gws_remove_group_member"
DESCRIPTION = ("Remove a member from a Google Workspace group. To remove a user from MANY groups, "
               "use the `bulk` tool rather than calling this once per group.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "group": {"type": "string", "description": "the group's email address or id"},
        "member": {"type": "string", "description": "the member's email or id to remove"},
    },
    "required": ["group", "member"],
    "additionalProperties": False,
}


def run(ctx, group: str = "", member: str = "", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_delete
    from ._gws_write import err_msg, api_error
    g, m = (group or "").strip(), (member or "").strip()
    if not (g and m):
        return {"ok": False, "error": "both group and member are required"}
    try:
        res = scoped_delete(ctx, "gws", f"/admin/directory/v1/groups/{g}/members/{m}")
    except HttpError as e:
        if getattr(e, "status", None) == 404:
            return {"ok": False, "error": f"'{m}' is not a member of '{g}' (or the group doesn't exist)"}
        return {"ok": False, "error": err_msg(e)}
    blocked = api_error(res)
    if blocked:
        return {"ok": False, "error": blocked}
    return {"ok": True, "group": g, "removed": m}
