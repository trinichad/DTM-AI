"""Create a Google Workspace Shared Drive via the Drive API (D-118)."""
from __future__ import annotations

import uuid
from typing import Any

NAME = "gws_create_shared_drive"
DESCRIPTION = ("Create a Google Workspace Shared Drive (a team drive with shared ownership). Add "
               "members afterwards with gws_add_shared_drive_member. Returns the new drive's id.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string", "description": "name for the new Shared Drive"}},
    "required": ["name"],
    "additionalProperties": False,
}


def run(ctx, name: str = "", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_write
    from ._gws_write import err_msg, api_error
    nm = (name or "").strip()
    if not nm:
        return {"ok": False, "error": "name is required"}
    # Drive requires a unique requestId per create (idempotency key).
    path = f"/drive/v3/drives?requestId={uuid.uuid4().hex}"
    try:
        res = scoped_write(ctx, "gws", path, body={"name": nm}, method="POST")
    except HttpError as e:
        return {"ok": False, "error": err_msg(e)}
    blocked = api_error(res)
    if blocked:
        return {"ok": False, "error": blocked}
    drive_id = res.get("id") if isinstance(res, dict) else None
    return {"ok": True, "created": nm, "drive_id": drive_id,
            "note": "add members with gws_add_shared_drive_member (pass this drive_id)"}
