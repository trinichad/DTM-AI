"""Create a Google Workspace group — Directory API POST (D-118)."""
from __future__ import annotations

from typing import Any

NAME = "gws_create_group"
DESCRIPTION = ("Create a Google Workspace group (a distribution / security group with an email "
               "address). Add members afterwards with gws_add_group_member.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "email": {"type": "string", "description": "the group's email address, e.g. sales@acme.com"},
        "name": {"type": "string", "description": "display name for the group"},
        "description": {"type": "string", "description": "optional description"},
    },
    "required": ["email", "name"],
    "additionalProperties": False,
}


def run(ctx, email: str = "", name: str = "", description: str = "", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_write
    from ._gws_write import err_msg, api_error
    email = (email or "").strip()
    if "@" not in email or " " in email:
        return {"ok": False, "error": f"'{email}' is not a valid group email address"}
    body: dict[str, Any] = {"email": email, "name": (name or email.split("@")[0]).strip()}
    if (description or "").strip():
        body["description"] = description.strip()
    try:
        created = scoped_write(ctx, "gws", "/admin/directory/v1/groups", body=body, method="POST")
    except HttpError as e:
        if getattr(e, "status", None) == 409:
            return {"ok": False, "error": f"a group with the address '{email}' already exists"}
        return {"ok": False, "error": err_msg(e)}
    blocked = api_error(created)
    if blocked:
        return {"ok": False, "error": blocked}
    return {"ok": True, "created": email, "name": body["name"],
            "note": "add members with gws_add_group_member"}
