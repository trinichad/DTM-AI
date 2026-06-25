"""Get a Proofpoint Essentials user's detail (incl. sender lists) (D-86)."""
from __future__ import annotations

from typing import Any

from . import _proofpoint_common as _p

NAME = "proofpoint_get_user"
DESCRIPTION = ("Get Proofpoint Essentials user(s) in an org (`domain`) — profile, license, aliases, "
               "and safe/blocked sender lists. Pass `email` for one user or `emails` (a list) to "
               "fetch MANY in ONE call — do NOT call this tool once per user.")
SOURCE = "proofpoint"
GROUP = "proofpoint"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "domain": {"type": "string", "description": "the org's primary domain"},
        "email": {"type": "string", "description": "the user's email address"},
        "emails": {"type": "array", "items": {"type": "string"},
                   "description": "fetch MANY users in ONE call — a list of email addresses in the "
                                  "same org; each user's detail comes back together. Use this "
                                  "instead of calling the tool once per user."},
    },
    "required": ["domain"],
    "additionalProperties": False,
}


def run(ctx, domain: str, email: str = "", emails: Any = None, **_: Any):
    d = (domain or "").strip()
    if not _p.valid_domain(d):
        return {"ok": False, "error": "give a valid domain"}
    client = ctx.client("proofpoint")
    wanted = [str(x).strip() for x in (emails or []) if str(x).strip()]
    if wanted:                                         # batch lookup (D-110) — one call, many users
        results = ctx.map_progress(wanted, lambda e: _one(client, d, e))
        return {"ok": True, "domain": d, "users_checked": len(results), "results": results}
    return _one(client, d, email)


def _one(client, domain: str, email: str) -> dict:
    e = (email or "").strip()
    if not _p.valid_email(e):
        return {"ok": False, "email": e, "error": "give a valid email address"}
    r = client.get(f"/orgs/{domain}/users/{e}")
    if isinstance(r, dict) and "email" not in r:       # tag so a batch row is attributable
        r = {**r, "email": e}
    return r
