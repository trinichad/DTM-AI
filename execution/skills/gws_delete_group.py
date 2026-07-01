"""Delete a Google Workspace group — Directory API DELETE (D-118)."""
from __future__ import annotations

from typing import Any

NAME = "gws_delete_group"
DESCRIPTION = ("Delete a Google Workspace group (by email or id). This removes the group and its "
               "membership list; it does not affect the member accounts themselves.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"group": {"type": "string", "description": "the group's email address or id"}},
    "required": ["group"],
    "additionalProperties": False,
}


def run(ctx, group: str = "", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_delete
    from ._gws_write import err_msg, api_error
    g = (group or "").strip()
    if not g:
        return {"ok": False, "error": "group (email or id) is required"}
    try:
        res = scoped_delete(ctx, "gws", f"/admin/directory/v1/groups/{g}")
    except HttpError as e:
        if getattr(e, "status", None) == 404:
            return {"ok": False, "error": f"group '{g}' not found"}
        return {"ok": False, "error": err_msg(e)}
    blocked = api_error(res)
    if blocked:
        return {"ok": False, "error": blocked}
    return {"ok": True, "deleted": g}
