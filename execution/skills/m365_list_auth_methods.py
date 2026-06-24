"""Which MFA/authentication methods a user has registered (D-60; SOP: m365-graph)."""
from __future__ import annotations

from typing import Any

NAME = "m365_list_auth_methods"
DESCRIPTION = ("Show WHICH authentication methods a user has registered — Microsoft "
               "Authenticator, phone (SMS/call) with the number, FIDO2 security key, Windows "
               "Hello, TOTP authenticator app, Temporary Access Pass. Pass `user` for one person "
               "or `users` (a list) to check MANY in ONE call — do NOT call this tool once per "
               "person. Use with m365_mfa_status: that says IF MFA is on, this says WITH WHAT. "
               "Password and recovery email are listed separately (they are not MFA).")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address (UPN)"},
        "users": {"type": "array", "items": {"type": "string"},
                  "description": "check MANY users in ONE call — a list of sign-in addresses; "
                                 "their registered methods come back together. Use this instead "
                                 "of calling the tool once per user."},
    },
    "additionalProperties": False,
}

# @odata.type → (friendly name, counts as MFA, detail picker)
_KINDS: dict[str, tuple] = {
    "#microsoft.graph.microsoftAuthenticatorAuthenticationMethod":
        ("Microsoft Authenticator app", True,
         lambda m: m.get("displayName")),
    "#microsoft.graph.phoneAuthenticationMethod":
        ("phone (SMS / voice call)", True,
         lambda m: f"{m.get('phoneType')}: {m.get('phoneNumber')}"),
    "#microsoft.graph.fido2AuthenticationMethod":
        ("FIDO2 security key / passkey", True,
         lambda m: m.get("model") or m.get("displayName")),
    "#microsoft.graph.windowsHelloForBusinessAuthenticationMethod":
        ("Windows Hello for Business", True,
         lambda m: m.get("displayName")),
    "#microsoft.graph.softwareOathAuthenticationMethod":
        ("authenticator app (TOTP, third-party)", True,
         lambda m: None),
    "#microsoft.graph.temporaryAccessPassAuthenticationMethod":
        ("Temporary Access Pass", True,
         lambda m: "currently usable" if m.get("isUsable") else "not usable"),
    "#microsoft.graph.emailAuthenticationMethod":
        ("recovery email (password reset only — NOT MFA)", False,
         lambda m: m.get("emailAddress")),
    "#microsoft.graph.passwordAuthenticationMethod":
        ("password (NOT MFA)", False,
         lambda m: None),
}


def run(ctx, user: str = "", users: Any = None, **_: Any):
    wanted = [str(u).strip() for u in (users or []) if str(u).strip()]
    if wanted:                                         # batch lookup (D-110) — one call, many users
        results = [_one(ctx, u) for u in wanted]
        return {"ok": True, "users_checked": len(results), "results": results}
    return _one(ctx, user)


def _one(ctx, user: str) -> dict:
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read
    from . import _graph_common as g
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "user": user, "error": f"'{user}' is not a sign-in address"}
    try:
        data = scoped_read(ctx, "m365", f"/users/{user}/authentication/methods")
    except HttpError as exc:
        return g.err403(exc, "reading authentication methods",
                        "UserAuthenticationMethod.Read.All")
    bad = g.fail(data)
    if bad:
        return {**bad, "user": user}

    mfa, other = [], []
    for m in g.rows(data):
        otype = str(m.get("@odata.type") or "")
        name, is_mfa, pick = _KINDS.get(otype, (otype.rsplit(".", 1)[-1] or "unknown method",
                                                True, lambda _m: None))
        detail = pick(m)
        row = {"method": name, **({"detail": str(detail)} if detail else {})}
        (mfa if is_mfa else other).append(row)

    out: dict[str, Any] = {"ok": True, "user": user,
                           "mfa_methods": mfa, "other_methods": other,
                           "summary": (f"{len(mfa)} MFA method(s) registered"
                                       if mfa else "NO MFA methods registered")}
    if not mfa:
        out["note"] = ("the user has nothing usable for MFA — even with MFA enforced they'll "
                       "be prompted to register at next sign-in; add a phone with "
                       "m365_add_phone_auth to pre-provision one")
    return out
