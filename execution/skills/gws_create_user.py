"""Create a Google Workspace user via the Admin SDK Directory API (D-118).

The email address IS the sign-in (primaryEmail); first/last name are required (ASK, never invent);
the initial password is generated server-side by `secrets`, never by the LLM.
"""
from __future__ import annotations

from typing import Any

NAME = "gws_create_user"
DESCRIPTION = ("Create a Google Workspace user. The email address becomes the sign-in (primaryEmail). "
               "first_name and last_name are REQUIRED — if the user didn't give them, ASK; never "
               "invent them. Pass `password` to set a specific initial password; otherwise a strong "
               "one is generated and returned once. By default the user must change it at first "
               "sign-in. Optionally place them in an org unit with `org_unit_path`. The account has "
               "NO license — assign one with gws_assign_license.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "email": {"type": "string", "description": "the new user's email = sign-in, e.g. jane@acme.com"},
        "first_name": {"type": "string", "description": "first name — ASK if not given, never invent"},
        "last_name": {"type": "string", "description": "last name — ASK if not given, never invent"},
        "password": {"type": "string",
                     "description": "initial password (optional — a strong one is generated when "
                                    "omitted; Google requires at least 8 characters)"},
        "must_change": {"type": "boolean",
                        "description": "require a password change at first sign-in (default true)"},
        "org_unit_path": {"type": "string",
                          "description": "org unit to place the user in, e.g. /Sales (optional, "
                                         "default /)"},
    },
    "required": ["email", "first_name", "last_name"],
    "additionalProperties": False,
}


def run(ctx, email: str, first_name: str, last_name: str, password: str = "",
        must_change: bool = True, org_unit_path: str = "", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read, scoped_write
    from ._gws_write import gen_password, err_msg, api_error
    email = (email or "").strip()
    if "@" not in email or " " in email:
        return {"ok": False, "error": f"'{email}' is not a valid email address"}
    first, last = (first_name or "").strip(), (last_name or "").strip()
    if not (first and last):
        return {"ok": False, "error": "first_name and last_name are required — ask the user"}
    owner_pw = (password or "").strip()
    if owner_pw and len(owner_pw) < 8:
        return {"ok": False, "error": "the password must be at least 8 characters (Google minimum)"}
    pw = owner_pw or gen_password()
    body: dict[str, Any] = {
        "primaryEmail": email,
        "name": {"givenName": first, "familyName": last},
        "password": pw,
        "changePasswordAtNextLogin": bool(must_change),
    }
    if (org_unit_path or "").strip():
        body["orgUnitPath"] = org_unit_path.strip()
    try:
        created = scoped_write(ctx, "gws", "/admin/directory/v1/users", body=body, method="POST")
    except HttpError as e:
        if getattr(e, "status", None) == 409:
            return {"ok": False, "error": f"a user with the address '{email}' already exists"}
        return {"ok": False, "error": err_msg(e)}
    blocked = api_error(created)
    if blocked:
        return {"ok": False, "error": blocked}

    # verify by re-reading (never report an unverified create); tolerate brief propagation lag
    check = scoped_read(ctx, "gws", f"/admin/directory/v1/users/{email}", {"projection": "basic"})
    verified = isinstance(check, dict) and bool(check.get("primaryEmail"))
    out: dict[str, Any] = {"ok": True, "created": email, "display_name": f"{first} {last}",
                           "org_unit": body.get("orgUnitPath", "/"), "verified": verified,
                           "license_note": "no license yet — assign one with gws_assign_license"}
    if not verified:
        out["note"] = "created but not yet readable back (propagation lag) — verify in the Admin console"
    if owner_pw:
        out["password_note"] = ("set to the password you provided"
                                + (" — user must change it at first sign-in" if must_change else ""))
    else:
        out["initial_password"] = pw
        out["password_note"] = ("share this securely"
                                + (" — user must change it at first sign-in" if must_change else ""))
    return out
