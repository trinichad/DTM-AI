"""Set a user's per-user MFA state (D-56; SOP: m365-graph)."""
from __future__ import annotations

from typing import Any

NAME = "m365_set_mfa"
DESCRIPTION = ("Set a user's PER-USER multifactor authentication state (the classic per-user "
               "MFA portal): 'enforced' = MFA required now, 'enabled' = required after the "
               "user registers (auto-promotes to enforced), 'disabled' = off. Check first "
               "with m365_mfa_status. Verifies the state before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_STATES = ("enforced", "enabled", "disabled")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address (UPN)"},
        "state": {"type": "string", "enum": list(_STATES),
                  "description": "the per-user MFA state to set (usually 'enforced')"},
    },
    "required": ["user", "state"],
    "additionalProperties": False,
}


def run(ctx, user: str, state: str, **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read, scoped_write
    from . import _graph_common as g
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a sign-in address"}
    want = (state or "").strip().lower()
    if want not in _STATES:
        return {"ok": False, "error": f"state must be one of: {', '.join(_STATES)}"}

    # /beta: Microsoft ships perUserMfaState only on the Graph beta endpoint (D-60) — v1.0
    # answers "Resource not found for the segment 'requirements'". Drop the prefix when it GAs.
    path = f"/beta/users/{user}/authentication/requirements"
    try:
        cur = scoped_read(ctx, "m365", path)
        bad = g.fail(cur)
        if bad:
            return bad
        before = str((cur or {}).get("perUserMfaState") or "unknown")
        if before == want:
            return {"ok": True, "user": user, "mfa_state": want,
                    "note": f"per-user MFA is already '{want}' — nothing to do"}
        r = scoped_write(ctx, "m365", path, body={"perUserMfaState": want}, method="PATCH")
        bad = g.fail(r)
        if bad:
            return bad
        check = scoped_read(ctx, "m365", path)
    except HttpError as exc:
        return g.err403(exc, "setting per-user MFA", "Policy.ReadWrite.AuthenticationMethod")

    now = str((check or {}).get("perUserMfaState") or "")
    if now != want:
        return {"ok": False, "step": "verify", "was": before, "now": now or "unknown",
                "error": "the call returned but the MFA state didn't change — check the "
                         "Entra admin center directly"}
    out: dict[str, Any] = {"ok": True, "user": user, "mfa_state": want, "was": before}
    if want == "disabled":
        out["warning"] = "MFA is now OFF for this user — make sure that's intended"
    elif want == "enabled":
        out["note"] = ("'enabled' becomes 'enforced' automatically once the user registers "
                       "an MFA method (add one with m365_add_phone_auth)")
    return out
