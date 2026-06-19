"""List a Proofpoint Essentials org's users (D-86)."""
from __future__ import annotations

from typing import Any

from . import _proofpoint_common as _p

NAME = "proofpoint_list_users"
DESCRIPTION = ("List the users provisioned on a Proofpoint Essentials organization (`domain` = the "
               "org's primary domain) — email, name, type/license, and status. Optional "
               "name_contains filter on email/name.")
SOURCE = "proofpoint"
GROUP = "proofpoint"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "domain": {"type": "string", "description": "the org's primary domain"},
        "name_contains": {"type": "string", "description": "case-insensitive email/name filter"},
    },
    "required": ["domain"],
    "additionalProperties": False,
}
_FIELDS = ("primary_email", "email", "firstname", "surname", "type", "license", "status",
           "is_active", "aliases")


def run(ctx, domain: str, name_contains: str = "", **_: Any):
    d = (domain or "").strip()
    if not _p.valid_domain(d):
        return {"ok": False, "error": "give a valid domain"}
    needle = (name_contains or "").strip().lower()
    out = []
    for u in _p.rows(ctx.client("proofpoint").get(f"/orgs/{d}/users"), "users"):
        blob = " ".join(str(u.get(k, "")) for k in ("primary_email", "email", "firstname", "surname"))
        if needle and needle not in blob.lower():
            continue
        out.append({k: u.get(k) for k in _FIELDS if k in u} or u)
    return out
