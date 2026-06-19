"""Delete a Proofpoint Essentials user (D-86) — destructive."""
from __future__ import annotations

from typing import Any

from . import _proofpoint_common as _p

NAME = "proofpoint_delete_user"
DESCRIPTION = ("Remove a user from a Proofpoint Essentials organization by `domain` + `email` "
               "(stops filtering their mail and frees the seat). Destructive, so it always needs a "
               "per-action approval. To merely disable, use proofpoint_update_user with "
               "active=false.")
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
    },
    "required": ["domain", "email"],
    "additionalProperties": False,
}


def run(ctx, domain: str, email: str, **_: Any):
    d, e = (domain or "").strip(), (email or "").strip()
    if not _p.valid_domain(d):
        return {"ok": False, "error": "give a valid domain"}
    if not _p.valid_email(e):
        return {"ok": False, "error": "give a valid email"}
    r = ctx.client("proofpoint").write_destructive("DELETE", f"/orgs/{d}/users/{e}", None)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "user": e, "note": "user removed from Proofpoint"}
