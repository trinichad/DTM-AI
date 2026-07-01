"""Restore (un-suspend) a Google Workspace user — Directory API PATCH (D-118)."""
from __future__ import annotations

from typing import Any

NAME = "gws_restore_user"
DESCRIPTION = ("Restore a suspended Google Workspace user — re-enables sign-in. The reverse of "
               "gws_suspend_user.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"user": {"type": "string", "description": "the user's primary email or id"}},
    "required": ["user"],
    "additionalProperties": False,
}


def run(ctx, user: str = "", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_write
    from ._gws_write import err_msg, api_error
    u = (user or "").strip()
    if not u:
        return {"ok": False, "error": "user (email or id) is required"}
    try:
        res = scoped_write(ctx, "gws", f"/admin/directory/v1/users/{u}",
                           body={"suspended": False}, method="PATCH")
    except HttpError as e:
        if getattr(e, "status", None) == 404:
            return {"ok": False, "error": f"user '{u}' not found"}
        return {"ok": False, "error": err_msg(e)}
    blocked = api_error(res)
    if blocked:
        return {"ok": False, "error": blocked}
    return {"ok": True, "user": u, "suspended": False, "note": "sign-in re-enabled"}
