"""Add a member to a Google Workspace group — Directory API POST (D-118)."""
from __future__ import annotations

from typing import Any

NAME = "gws_add_group_member"
DESCRIPTION = ("Add a member (user or another group) to a Google Workspace group, with a role. To "
               "add MANY members, use the `bulk` tool rather than calling this once per member.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "group": {"type": "string", "description": "the group's email address or id"},
        "member": {"type": "string", "description": "the member's email (a user or a group)"},
        "role": {"type": "string", "enum": ["MEMBER", "MANAGER", "OWNER"],
                 "description": "member role (default MEMBER)"},
    },
    "required": ["group", "member"],
    "additionalProperties": False,
}


def run(ctx, group: str = "", member: str = "", role: str = "MEMBER", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_write
    from ._gws_write import err_msg, api_error
    g, m = (group or "").strip(), (member or "").strip()
    if not (g and m):
        return {"ok": False, "error": "both group and member are required"}
    role = role.upper() if role.upper() in ("MEMBER", "MANAGER", "OWNER") else "MEMBER"
    try:
        res = scoped_write(ctx, "gws", f"/admin/directory/v1/groups/{g}/members",
                           body={"email": m, "role": role}, method="POST")
    except HttpError as e:
        st = getattr(e, "status", None)
        if st == 404:
            return {"ok": False, "error": f"group '{g}' or member '{m}' not found"}
        if st == 409:
            return {"ok": False, "error": f"'{m}' is already a member of '{g}'"}
        return {"ok": False, "error": err_msg(e)}
    blocked = api_error(res)
    if blocked:
        return {"ok": False, "error": blocked}
    return {"ok": True, "group": g, "member": m, "role": role}
