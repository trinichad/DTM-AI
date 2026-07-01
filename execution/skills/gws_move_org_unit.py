"""Move a Google Workspace user to a different org unit — Directory API PATCH (D-118).

Org unit membership drives policy and (often) licensing, so this is how you apply a different
policy set to a user. List OUs with gws_list_org_units.
"""
from __future__ import annotations

from typing import Any

NAME = "gws_move_org_unit"
DESCRIPTION = ("Move a Google Workspace user into a different organizational unit (OU), e.g. /Sales "
               "or /Disabled Accounts. OU membership drives policies and often licensing. Find OU "
               "paths with gws_list_org_units.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's primary email or id"},
        "org_unit_path": {"type": "string", "description": "destination OU path, e.g. /Sales"},
    },
    "required": ["user", "org_unit_path"],
    "additionalProperties": False,
}


def run(ctx, user: str = "", org_unit_path: str = "", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_write
    from ._gws_write import err_msg, api_error
    u = (user or "").strip()
    ou = (org_unit_path or "").strip()
    if not u:
        return {"ok": False, "error": "user (email or id) is required"}
    if not ou.startswith("/"):
        return {"ok": False, "error": "org_unit_path must start with '/', e.g. /Sales"}
    try:
        res = scoped_write(ctx, "gws", f"/admin/directory/v1/users/{u}",
                           body={"orgUnitPath": ou}, method="PATCH")
    except HttpError as e:
        if getattr(e, "status", None) == 404:
            return {"ok": False, "error": f"user '{u}' or org unit '{ou}' not found"}
        return {"ok": False, "error": err_msg(e)}
    blocked = api_error(res)
    if blocked:
        return {"ok": False, "error": blocked}
    return {"ok": True, "user": u, "org_unit_path": ou}
