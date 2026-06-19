"""Create a Proofpoint Essentials user (D-86)."""
from __future__ import annotations

from typing import Any

from . import _proofpoint_common as _p

NAME = "proofpoint_create_user"
DESCRIPTION = ("Provision a user on a Proofpoint Essentials organization (so their mail is "
               "filtered). Give the org `domain`, the user's `email`, first and last name. "
               "Optional: type (e.g. 'end_user'/'silent'), aliases. Chains naturally into M365 "
               "onboarding.")
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
        "email": {"type": "string", "description": "the new user's email"},
        "first_name": {"type": "string"},
        "last_name": {"type": "string"},
        "type": {"type": "string", "description": "user/license type (optional)"},
        "aliases": {"type": "array", "items": {"type": "string"}, "description": "email aliases (optional)"},
    },
    "required": ["domain", "email", "first_name", "last_name"],
    "additionalProperties": False,
}


def run(ctx, domain: str, email: str, first_name: str, last_name: str, type: str = "",
        aliases: Any = None, **_: Any):
    d, e = (domain or "").strip(), (email or "").strip()
    if not _p.valid_domain(d):
        return {"ok": False, "error": "give a valid domain"}
    if not _p.valid_email(e):
        return {"ok": False, "error": "give a valid email"}
    fn, ln = (first_name or "").strip(), (last_name or "").strip()
    if not (fn and ln):
        return {"ok": False, "error": "give first and last name"}
    body: dict[str, Any] = {"primary_email": e, "firstname": fn[:64], "surname": ln[:64]}
    if (type or "").strip():
        body["type"] = type.strip()
    if isinstance(aliases, list) and aliases:
        body["aliases"] = [str(a).strip() for a in aliases if _p.valid_email(str(a))]
    r = ctx.client("proofpoint").write("POST", f"/orgs/{d}/users", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "user": r, "note": "user provisioned on Proofpoint"}
