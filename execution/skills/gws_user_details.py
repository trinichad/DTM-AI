"""Get full details for one Google Workspace user via the Admin SDK Directory API (D-118) — read-only."""
from __future__ import annotations

from typing import Any

NAME = "gws_user_details"
DESCRIPTION = ("Get full details for ONE Google Workspace user by email (or id): name, org unit, "
               "suspended/admin/2SV state, aliases, last login, creation time, recovery info, "
               "manager. Use gws_list_users to find users; use this to inspect one.")
SOURCE = "gws"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's primary email address or id"},
    },
    "required": ["user"],
    "additionalProperties": False,
}


def _slim(u: dict) -> dict:
    name = (u.get("name") or {}) if isinstance(u.get("name"), dict) else {}
    out: dict[str, Any] = {
        "primaryEmail": u.get("primaryEmail"),
        "fullName": name.get("fullName"),
        "givenName": name.get("givenName"),
        "familyName": name.get("familyName"),
        "suspended": bool(u.get("suspended")),
        "isAdmin": bool(u.get("isAdmin")),
        "isEnrolledIn2Sv": bool(u.get("isEnrolledIn2Sv")),
        "orgUnitPath": u.get("orgUnitPath"),
        "lastLoginTime": u.get("lastLoginTime"),
        "creationTime": u.get("creationTime"),
    }
    if u.get("aliases"):
        out["aliases"] = u["aliases"]
    if u.get("recoveryEmail"):
        out["recoveryEmail"] = u["recoveryEmail"]
    if u.get("suspensionReason"):
        out["suspensionReason"] = u["suspensionReason"]
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def run(ctx, user: str = "", **_: Any):
    from execution.clients.scopes import scoped_read
    u = (user or "").strip()
    if not u:
        return {"ok": False, "error": "user (email or id) is required"}
    data = scoped_read(ctx, "gws", f"/admin/directory/v1/users/{u}", {"projection": "full"})
    if isinstance(data, dict) and data.get("error"):
        return data
    if not isinstance(data, dict) or not data.get("primaryEmail"):
        return {"ok": False, "error": f"user '{u}' not found"}
    return {"ok": True, "user": _slim(data)}
