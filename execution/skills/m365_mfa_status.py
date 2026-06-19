"""Check per-user MFA state — one user or the whole client (D-56; SOP: m365-graph)."""
from __future__ import annotations

from typing import Any

NAME = "m365_mfa_status"
DESCRIPTION = ("Check PER-USER MFA: pass `user` for one person's state (enforced / enabled / "
               "disabled), or leave it empty to sweep the client and report who has MFA "
               "enforced and who does NOT. Sweeps are capped by `limit` (default 100).")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "one user's sign-in address (optional — "
                                                  "omit to check everyone)"},
        "limit": {"type": "integer",
                  "description": "max users for a whole-client sweep (default 100, max 500)"},
    },
    "additionalProperties": False,
}


def _state(ctx, upn: str) -> str:
    from ..clients.scopes import scoped_read
    from . import _graph_common as g
    # /beta: perUserMfaState exists only on the Graph beta endpoint (D-60)
    r = scoped_read(ctx, "m365", f"/beta/users/{upn}/authentication/requirements")
    if g.fail(r):
        return f"error: {g.fail(r)['error'][:120]}"
    return str((r or {}).get("perUserMfaState") or "unknown")


def run(ctx, user: str = "", limit: int = 100, **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read
    from . import _graph_common as g
    try:
        if (user or "").strip():
            upn = user.strip()
            s = _state(ctx, upn)
            if s.startswith("error:"):
                return {"ok": False, "user": upn, "error": s[7:]}
            return {"ok": True, "user": upn, "mfa_state": s,
                    "meaning": {"enforced": "MFA is required at every sign-in",
                                "enabled": "MFA required once the user registers (will "
                                           "auto-promote to enforced)",
                                "disabled": "per-user MFA is OFF",
                                "unknown": "state not reported"}.get(s, s)}

        limit = max(1, min(int(limit or 100), 500))
        data = scoped_read(ctx, "m365", "/users",
                           {"$select": "userPrincipalName,accountEnabled", "$top": limit})
        bad = g.fail(data)
        if bad:
            return bad
        users = [str(u.get("userPrincipalName")) for u in g.rows(data)
                 if u.get("userPrincipalName")]
        buckets: dict[str, list] = {"enforced": [], "enabled": [], "disabled": [],
                                    "unknown": [], "errors": []}
        for upn in users:
            s = _state(ctx, upn)
            if s.startswith("error:"):
                buckets["errors"].append({"user": upn, "error": s[7:]})
            else:
                buckets.setdefault(s, buckets["unknown"]).append(upn)
        out: dict[str, Any] = {
            "ok": True, "users_checked": len(users),
            "mfa_enforced": buckets["enforced"],
            "mfa_enabled_not_enforced": buckets["enabled"],
            "mfa_disabled": buckets["disabled"],
            "summary": {"enforced": len(buckets["enforced"]),
                        "enabled": len(buckets["enabled"]),
                        "disabled": len(buckets["disabled"])}}
        if buckets["unknown"]:
            out["unknown"] = buckets["unknown"]
        if buckets["errors"]:
            out["errors"] = buckets["errors"]
        more = isinstance(data, dict) and data.get("@odata.nextLink")
        if more or len(users) >= limit:
            out["note"] = (f"checked the first {len(users)} users — raise `limit` (max 500) "
                           f"to sweep more")
        return out
    except HttpError as exc:
        # docs-audit correction: the sweep lists /users (User.Read.All) AND reads each
        # authentication/requirements (Policy.Read.All) — name both (D-66)
        return g.err403(exc, "reading per-user MFA", "User.Read.All + Policy.Read.All")
