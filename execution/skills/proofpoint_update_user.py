"""Update a Proofpoint Essentials user — incl. disabling for offboarding (D-86)."""
from __future__ import annotations

from typing import Any

from . import _proofpoint_common as _p

NAME = "proofpoint_update_user"
DESCRIPTION = ("Update a Proofpoint Essentials user by org `domain` + `email`. Change name, type/"
               "license, aliases, or set active=false to DISABLE filtering for them (offboarding). "
               "Only the fields you pass change. Pass `emails` (a list) to apply the SAME changes "
               "to MANY users in ONE call — do NOT call this tool once per user.")
SOURCE = "proofpoint"
GROUP = "proofpoint"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "domain": {"type": "string", "description": "the org's primary domain"},
        "email": {"type": "string", "description": "the user's email"},
        "emails": {"type": "array", "items": {"type": "string"},
                   "description": "apply the SAME changes to MANY users in ONE call — a list of "
                                  "user emails in the same org; results come back together. Use "
                                  "this instead of calling the tool once per user."},
        "first_name": {"type": "string"},
        "last_name": {"type": "string"},
        "type": {"type": "string", "description": "user/license type"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "active": {"type": "boolean", "description": "false to disable the user (offboarding)"},
    },
    "required": ["domain"],
    "additionalProperties": False,
}


def run(ctx, domain: str, email: str = "", emails: Any = None, first_name: str = "",
        last_name: str = "", type: str = "", aliases: Any = None, active: Any = None, **_: Any):
    d = (domain or "").strip()
    if not _p.valid_domain(d):
        return {"ok": False, "error": "give a valid domain"}
    wanted = [str(x).strip() for x in (emails or []) if str(x).strip()]
    if wanted:                                         # batch (D-110) — same changes, many users
        results = ctx.map_progress(
            wanted[:500],
            lambda e: _one(ctx, d, e, first_name, last_name, type, aliases, active))
        return {"ok": any(r.get("ok") for r in results), "users_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, d, email, first_name, last_name, type, aliases, active)


def _one(ctx, d: str, email: str, first_name: str = "", last_name: str = "", type: str = "",
         aliases: Any = None, active: Any = None) -> dict:
    e = (email or "").strip()
    if not _p.valid_email(e):
        return {"ok": False, "user": e, "error": "give a valid email"}
    body: dict[str, Any] = {}
    if (first_name or "").strip():
        body["firstname"] = first_name.strip()[:64]
    if (last_name or "").strip():
        body["surname"] = last_name.strip()[:64]
    if (type or "").strip():
        body["type"] = type.strip()
    if isinstance(aliases, list):
        body["aliases"] = [str(a).strip() for a in aliases if _p.valid_email(str(a))]
    if active is not None:
        body["is_active"] = bool(active)
    if not body:
        return {"ok": False, "user": e, "error": "give at least one field to change"}
    r = ctx.client("proofpoint").write("PUT", f"/orgs/{d}/users/{e}", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "user": e, "error": r["error"]}
    return {"ok": True, "user": r, "note": "user updated"}
