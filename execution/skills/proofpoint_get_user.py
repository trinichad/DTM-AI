"""Get a Proofpoint Essentials user's detail (incl. sender lists) (D-86)."""
from __future__ import annotations

from typing import Any

from . import _proofpoint_common as _p

NAME = "proofpoint_get_user"
DESCRIPTION = ("Get one Proofpoint Essentials user by org `domain` and `email` — profile, license, "
               "aliases, and their safe/blocked sender lists.")
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
    },
    "required": ["domain", "email"],
    "additionalProperties": False,
}


def run(ctx, domain: str, email: str, **_: Any):
    d, e = (domain or "").strip(), (email or "").strip()
    if not _p.valid_domain(d):
        return {"ok": False, "error": "give a valid domain"}
    if not _p.valid_email(e):
        return {"ok": False, "error": "give a valid email address"}
    return ctx.client("proofpoint").get(f"/orgs/{d}/users/{e}")
