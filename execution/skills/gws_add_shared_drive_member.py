"""Add a member to a Google Workspace Shared Drive via the Drive API (D-118)."""
from __future__ import annotations

from typing import Any

NAME = "gws_add_shared_drive_member"
DESCRIPTION = ("Grant a user (or group) access to a Shared Drive, with a role. Pass the `drive_id` "
               "(from gws_list_shared_drives / gws_create_shared_drive), the member email, and a "
               "role: organizer (manage), fileOrganizer (manage content), writer, commenter, or "
               "reader. To add MANY members, use the `bulk` tool.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

_ROLES = ("organizer", "fileOrganizer", "writer", "commenter", "reader")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "drive_id": {"type": "string", "description": "the Shared Drive's id"},
        "member": {"type": "string", "description": "the member's email (user or group)"},
        "role": {"type": "string", "enum": list(_ROLES),
                 "description": "access role (default writer)"},
        "member_type": {"type": "string", "enum": ["user", "group"],
                        "description": "member type (default user)"},
    },
    "required": ["drive_id", "member"],
    "additionalProperties": False,
}


def run(ctx, drive_id: str = "", member: str = "", role: str = "writer",
        member_type: str = "user", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_write
    from ._gws_write import err_msg, api_error
    did, m = (drive_id or "").strip(), (member or "").strip()
    if not (did and m):
        return {"ok": False, "error": "both drive_id and member are required"}
    role = role if role in _ROLES else "writer"
    mtype = member_type if member_type in ("user", "group") else "user"
    path = (f"/drive/v3/files/{did}/permissions"
            "?supportsAllDrives=true&useDomainAdminAccess=true&sendNotificationEmail=false")
    body = {"type": mtype, "role": role, "emailAddress": m}
    try:
        res = scoped_write(ctx, "gws", path, body=body, method="POST")
    except HttpError as e:
        if getattr(e, "status", None) == 404:
            return {"ok": False, "error": f"shared drive '{did}' not found"}
        return {"ok": False, "error": err_msg(e)}
    blocked = api_error(res)
    if blocked:
        return {"ok": False, "error": blocked}
    return {"ok": True, "drive_id": did, "member": m, "role": role}
