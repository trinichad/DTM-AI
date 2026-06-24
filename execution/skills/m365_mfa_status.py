"""Check per-user MFA state — one user or the whole client (D-56; SOP: m365-graph)."""
from __future__ import annotations

from typing import Any

NAME = "m365_mfa_status"
DESCRIPTION = ("Check PER-USER MFA: pass `user` for one person's state (enforced / enabled / "
               "disabled); pass `users` (a list) to check MANY specific people in ONE call — do "
               "NOT call this tool once per person; or leave both empty to sweep the whole client "
               "and report who has MFA enforced and who does NOT. Sweeps are capped by `limit` "
               "(default 100).")
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
        "users": {"type": "array", "items": {"type": "string"},
                  "description": "check MANY specific users in ONE call — a list of sign-in "
                                 "addresses; their states come back together. Use this instead of "
                                 "calling the tool once per user."},
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


def _buckets() -> dict[str, list]:
    return {"enforced": [], "enabled": [], "disabled": [], "unknown": [], "errors": []}


def _summarize(buckets: dict, **extra: Any) -> dict:
    out: dict[str, Any] = {
        "ok": True,
        "mfa_enforced": buckets["enforced"],
        "mfa_enabled_not_enforced": buckets["enabled"],
        "mfa_disabled": buckets["disabled"],
        "summary": {"enforced": len(buckets["enforced"]),
                    "enabled": len(buckets["enabled"]),
                    "disabled": len(buckets["disabled"])},
        **extra}
    if buckets["unknown"]:
        out["unknown"] = buckets["unknown"]
    if buckets["errors"]:
        out["errors"] = buckets["errors"]
    return out


def _check_list(ctx, upns: list[str]) -> dict:
    """Check a SPECIFIC list of users in one call (D-110) — collapses N per-user tool rounds."""
    buckets = _buckets()
    for upn in upns:
        s = _state(ctx, upn)
        if s.startswith("error:"):
            buckets["errors"].append({"user": upn, "error": s[7:]})
        else:
            buckets.setdefault(s, buckets["unknown"]).append(upn)
    return _summarize(buckets, users_checked=len(upns))


def run(ctx, user: str = "", users: Any = None, limit: int = 100, **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read
    from . import _graph_common as g
    try:
        wanted = [str(u).strip() for u in (users or []) if str(u).strip()]
        if wanted:
            return _check_list(ctx, wanted[:500])

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
        buckets = _buckets()
        for upn in users:
            s = _state(ctx, upn)
            if s.startswith("error:"):
                buckets["errors"].append({"user": upn, "error": s[7:]})
            else:
                buckets.setdefault(s, buckets["unknown"]).append(upn)
        out = _summarize(buckets, users_checked=len(users))
        more = isinstance(data, dict) and data.get("@odata.nextLink")
        if more or len(users) >= limit:
            out["note"] = (f"checked the first {len(users)} users — raise `limit` (max 500) "
                           f"to sweep more")
        return out
    except HttpError as exc:
        # docs-audit correction: the sweep lists /users (User.Read.All) AND reads each
        # authentication/requirements (Policy.Read.All) — name both (D-66)
        return g.err403(exc, "reading per-user MFA", "User.Read.All + Policy.Read.All")
