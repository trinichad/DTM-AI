"""Create a Microsoft 365 / Entra user via Graph (D-55; SOP: m365-graph).

Lessons baked in (the AI-Test misfire): the User ID (userPrincipalName) IS the requested
email address — never derived from the display name; first/last name are always set (the
agent must ASK when not given, never invent); the initial password is generated server-side
by `secrets`, never by the LLM.
"""
from __future__ import annotations

import secrets
import string
from typing import Any

NAME = "m365_create_user"
DESCRIPTION = ("Create a Microsoft 365 / Entra user. The email address you pass becomes BOTH "
               "the sign-in User ID and the email — they always match. first_name and "
               "last_name are REQUIRED: if the user didn't give them, ASK — never invent them "
               "(pass \"\" only if the user explicitly said no name). Optional profile fields "
               "are set when provided. Pass `password` to set a specific initial password; "
               "otherwise a strong one is generated and returned once. By default the user "
               "must change it at first sign-in (must_change). The account has NO license — "
               "assign one with m365_assign_license.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

# user-facing parameter → Graph property (set only when non-empty)
_PROFILE_FIELDS = {
    "job_title": "jobTitle",
    "department": "department",
    "office": "officeLocation",
    "mobile_phone": "mobilePhone",
    "street_address": "streetAddress",
    "city": "city",
    "state": "state",
    "postal_code": "postalCode",
    "country": "country",
}
_OPT = {k: {"type": "string", "description": d} for k, d in {
    "job_title": "job title (optional)",
    "department": "department (optional)",
    "office": "office location (optional)",
    "office_phone": "office/business phone (optional)",
    "mobile_phone": "mobile phone (optional)",
    "street_address": "street address (optional)",
    "city": "city (optional)",
    "state": "state or province (optional)",
    "postal_code": "zip / postal code (optional)",
    "country": "country (optional)",
}.items()}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "email": {"type": "string",
                  "description": "the new user's email address — becomes the sign-in User ID "
                                 "too, e.g. user@demodomain.com"},
        "first_name": {"type": "string",
                       "description": "first name — ASK the user if not given, never invent"},
        "last_name": {"type": "string",
                      "description": "last name — ASK the user if not given, never invent"},
        "display_name": {"type": "string",
                         "description": "display name (default: 'First Last')"},
        "password": {"type": "string",
                     "description": "initial password to set (optional — a strong one is "
                                    "generated when omitted; Microsoft rejects weak ones)"},
        "must_change": {"type": "boolean",
                        "description": "require a password change at first sign-in "
                                       "(default true)"},
        **_OPT,
    },
    "required": ["email", "first_name", "last_name"],
    "additionalProperties": False,
}


def _gen_password() -> str:
    """16 chars with all four classes — generated here, never by the model."""
    pools = (string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#$%*")
    chars = [secrets.choice(p) for p in pools]
    chars += [secrets.choice("".join(pools)) for _ in range(12)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def run(ctx, email: str, first_name: str, last_name: str, display_name: str = "",
        password: str = "", must_change: bool = True, office_phone: str = "", **kwargs: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read, scoped_write
    email = (email or "").strip()
    if "@" not in email or " " in email:
        return {"ok": False, "error": f"'{email}' is not a valid email address"}
    first, last = (first_name or "").strip(), (last_name or "").strip()
    name = ((display_name or "").strip() or f"{first} {last}".strip()
            or email.split("@")[0])

    owner_password = (password or "").strip()
    if owner_password and len(owner_password) < 8:
        return {"ok": False, "error": "the password must be at least 8 characters "
                                      "(Microsoft requires 8–256 with complexity)"}
    password = owner_password or _gen_password()
    body: dict[str, Any] = {
        "accountEnabled": True,
        "displayName": name,
        "userPrincipalName": email,             # User ID == email, always (D-55)
        "mailNickname": email.split("@")[0],
        "passwordProfile": {"password": password,
                            "forceChangePasswordNextSignIn": bool(must_change)},
    }
    if first:
        body["givenName"] = first
    if last:
        body["surname"] = last
    if (office_phone or "").strip():
        body["businessPhones"] = [office_phone.strip()]
    fields_set = []
    for param, graph_prop in _PROFILE_FIELDS.items():
        v = (kwargs.get(param) or "").strip() if isinstance(kwargs.get(param), str) else ""
        if v:
            body[graph_prop] = v
            fields_set.append(param)

    try:
        created = scoped_write(ctx, "m365", "/users", body=body, method="POST")
    except HttpError as e:
        if e.status == 403:
            return {"ok": False, "error":
                    "Graph refused (403) — the sign-in lacks write consent. Add "
                    "User.ReadWrite.All to M365_SCOPES on the M365 card and sign the client "
                    "in again."}
        if e.status == 400 and "userPrincipalName" in e.body and "exist" in e.body.lower():
            return {"ok": False, "error": f"a user with the address '{email}' already exists"}
        if e.status == 400 and "password" in e.body.lower():
            return {"ok": False, "error":
                    "Microsoft rejected the password (complexity policy: 8–256 chars, three of "
                    "upper/lower/digit/symbol, not containing the username) — pick a stronger one"}
        return {"ok": False, "error": f"Graph HTTP {e.status}: {e.body[:300]}"}
    if isinstance(created, dict) and created.get("error"):
        return {"ok": False, "error": str(created["error"])}

    # Verify by re-reading the user — never report an unverified create (D-43).
    try:
        check = scoped_read(ctx, "m365", f"/users/{email}",
                            {"$select": "id,displayName,userPrincipalName,givenName,surname"})
    except HttpError:
        check = None
    if not (isinstance(check, dict) and check.get("userPrincipalName")):
        return {"ok": False, "step": "verify",
                "error": f"the create call returned but '{email}' could not be read back — "
                         f"check Entra directly before retrying"}
    out: dict[str, Any] = {
        "ok": True, "created": email, "user_id": email, "display_name": name,
        "first_name": first or None, "last_name": last or None,
        "profile_fields_set": fields_set + (["office_phone"] if office_phone.strip() else []),
        "license_note": "no license yet — assign one with m365_assign_license"}
    if owner_password:
        # never echo an owner-supplied password back — it would land in chat history
        out["password_note"] = ("set to the password you provided"
                                + (" — the user must change it at first sign-in"
                                   if must_change else ""))
    else:
        out["initial_password"] = password
        out["password_note"] = ("share this securely"
                                + (" — the user must change it at first sign-in"
                                   if must_change else ""))
    return out
