"""Delete a Proofpoint Essentials user (D-86) — destructive."""
from __future__ import annotations

from typing import Any

from . import _proofpoint_common as _p

NAME = "proofpoint_delete_user"
DESCRIPTION = ("Remove a user from a Proofpoint Essentials organization by `domain` + `email` "
               "(stops filtering their mail and frees the seat). Pass `emails` (a list) to remove "
               "MANY users in ONE call — do NOT call this tool once per user. Destructive, so it "
               "always needs a per-action approval. To merely disable, use proofpoint_update_user "
               "with active=false.")
SOURCE = "proofpoint"
GROUP = "proofpoint"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "domain": {"type": "string", "description": "the org's primary domain"},
        "email": {"type": "string", "description": "the user's email to remove"},
        "emails": {"type": "array", "items": {"type": "string"},
                   "description": "remove MANY users in ONE call — a list of user emails in the "
                                  "same org; results come back together. Use this instead of "
                                  "calling the tool once per user."},
    },
    "required": ["domain"],
    "additionalProperties": False,
}


def run(ctx, domain: str, email: str = "", emails: Any = None, **_: Any):
    d = (domain or "").strip()
    if not _p.valid_domain(d):
        return {"ok": False, "error": "give a valid domain"}
    wanted = [str(x).strip() for x in (emails or []) if str(x).strip()]
    if wanted:                                         # batch (D-110) — many users, one call
        results = ctx.map_progress(wanted[:500], lambda e: _one(ctx, d, e))
        return {"ok": any(r.get("ok") for r in results), "users_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, d, email)


def _one(ctx, d: str, email: str) -> dict:
    e = (email or "").strip()
    if not _p.valid_email(e):
        return {"ok": False, "user": e, "error": "give a valid email"}
    r = ctx.client("proofpoint").write_destructive("DELETE", f"/orgs/{d}/users/{e}", None)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "user": e, "error": r["error"]}
    return {"ok": True, "user": e, "note": "user removed from Proofpoint"}
