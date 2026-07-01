"""Reset a Google Workspace user's password — Directory API PATCH (D-118).

The password is generated server-side by `secrets` (never by the LLM) unless the owner supplies one.
"""
from __future__ import annotations

from typing import Any

NAME = "gws_reset_password"
DESCRIPTION = ("Reset a Google Workspace user's password. A strong password is generated and returned "
               "once unless you pass `password`. By default the user must change it at next sign-in. "
               "Use for a locked-out user or as part of offboarding/securing an account.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's primary email or id"},
        "password": {"type": "string",
                     "description": "specific new password (optional — generated when omitted; "
                                    "Google requires at least 8 characters)"},
        "must_change": {"type": "boolean",
                        "description": "require a change at next sign-in (default true)"},
    },
    "required": ["user"],
    "additionalProperties": False,
}


def run(ctx, user: str = "", password: str = "", must_change: bool = True, **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_write
    from ._gws_write import gen_password, err_msg, api_error
    u = (user or "").strip()
    if not u:
        return {"ok": False, "error": "user (email or id) is required"}
    owner_pw = (password or "").strip()
    if owner_pw and len(owner_pw) < 8:
        return {"ok": False, "error": "the password must be at least 8 characters (Google minimum)"}
    pw = owner_pw or gen_password()
    try:
        res = scoped_write(ctx, "gws", f"/admin/directory/v1/users/{u}",
                           body={"password": pw, "changePasswordAtNextLogin": bool(must_change)},
                           method="PATCH")
    except HttpError as e:
        if getattr(e, "status", None) == 404:
            return {"ok": False, "error": f"user '{u}' not found"}
        return {"ok": False, "error": err_msg(e)}
    blocked = api_error(res)
    if blocked:
        return {"ok": False, "error": blocked}
    out: dict[str, Any] = {"ok": True, "user": u, "must_change": bool(must_change)}
    if owner_pw:
        out["password_note"] = "set to the password you provided"
    else:
        out["new_password"] = pw
        out["password_note"] = "share this securely"
    return out
