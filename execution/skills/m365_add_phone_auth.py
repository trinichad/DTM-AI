"""Add/update a user's phone authentication method (D-56; SOP: m365-graph)."""
from __future__ import annotations

import re
from typing import Any

NAME = "m365_add_phone_auth"
DESCRIPTION = ("Add (or update) a user's PHONE as an MFA authentication method — what the user "
               "gets texts/calls on for sign-in verification. US numbers can be given any "
               "common way (5551234567, 555-123-4567); other countries must include the "
               "country code like '+44 ...'. phone_type mobile (default), alternateMobile, or "
               "office. Verifies before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"            # an attacker-controlled auth phone = account takeover; reviewed
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_TYPES = ("mobile", "alternateMobile", "office")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address (UPN)"},
        "phone": {"type": "string", "description": "the phone number, e.g. '555-123-4567' "
                                                   "(US assumed) or '+1 5551234567'"},
        "phone_type": {"type": "string", "enum": list(_TYPES),
                       "description": "which slot (default mobile — the one used for MFA "
                                      "texts/calls)"},
    },
    "required": ["user", "phone"],
    "additionalProperties": False,
}


def normalize_phone(raw: str) -> tuple[str, str]:
    """→ (normalized '+1 5551234567', '') or ('', error). 10/11-digit input assumed US."""
    s = (raw or "").strip()
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return "", f"'{raw}' is not a phone number"
    if not plus:
        if len(digits) == 10:
            return f"+1 {digits}", ""
        if len(digits) == 11 and digits.startswith("1"):
            return f"+1 {digits[1:]}", ""
        return "", (f"'{raw}' is not a US number — include the country code "
                    f"(e.g. '+44 7911123456')")
    if digits.startswith("1") and len(digits) == 11:
        return f"+1 {digits[1:]}", ""
    m = re.match(r"^\+(\d{1,3})[\s.-]+(.+)$", s)         # explicit "+CC rest" given
    if m:
        rest = re.sub(r"\D", "", m.group(2))
        if rest:
            return f"+{m.group(1)} {rest}", ""
    return "", (f"can't split the country code in '{raw}' — write it as '+CC number' "
                f"with a space, e.g. '+44 7911123456'")


def run(ctx, user: str, phone: str, phone_type: str = "mobile", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read, scoped_write
    from . import _graph_common as g
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a sign-in address"}
    ptype = next((t for t in _TYPES if t.lower() == (phone_type or "").strip().lower()),
                 None)
    if not ptype:
        return {"ok": False, "error": f"phone_type must be one of: {', '.join(_TYPES)}"}
    number, e = normalize_phone(phone)
    if e:
        return {"ok": False, "error": e}

    base = f"/users/{user}/authentication/phoneMethods"
    scope = "UserAuthenticationMethod.ReadWrite.All"
    try:
        cur = scoped_read(ctx, "m365", base)
        bad = g.fail(cur)
        if bad:
            return bad
        existing = next((m for m in g.rows(cur)
                         if str(m.get("phoneType")) == ptype), None)
        if existing and str(existing.get("phoneNumber")) == number:
            return {"ok": True, "user": user, "phone": number, "phone_type": ptype,
                    "note": "that phone is already the user's method — nothing to do"}
        if existing:
            r = scoped_write(ctx, "m365", f"{base}/{existing.get('id')}",
                             body={"phoneNumber": number, "phoneType": ptype},
                             method="PATCH")
        else:
            r = scoped_write(ctx, "m365", base,
                             body={"phoneNumber": number, "phoneType": ptype},
                             method="POST")
        bad = g.fail(r)
        if bad:
            return bad

        # Auth-method reads are eventually-consistent — poll until the new number shows (D-104).
        def _now(c):
            return next((m for m in g.rows(c) if str(m.get("phoneType")) == ptype), None)
        _ok, check = g.settle(
            lambda: scoped_read(ctx, "m365", base),
            lambda c: str((_now(c) or {}).get("phoneNumber")) == number)
    except HttpError as exc:
        return g.err403(exc, "setting the phone method", scope)

    now = _now(check)
    if not (now and str(now.get("phoneNumber")) == number):
        return {"ok": False, "step": "verify", "pending": True,
                "error": "the call returned but the new phone number isn't showing yet — usually "
                         "propagation lag; re-check in Entra shortly"}
    return {"ok": True, "user": user, "phone": number, "phone_type": ptype,
            ("updated" if existing else "added"): True,
            "note": "the user can now receive MFA texts/calls on this number"}
