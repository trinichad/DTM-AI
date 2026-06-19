"""List a Proofpoint Essentials org's domains (D-86)."""
from __future__ import annotations

from typing import Any

from . import _proofpoint_common as _p

NAME = "proofpoint_list_domains"
DESCRIPTION = ("List the domains configured on a Proofpoint Essentials organization (`domain` = "
               "the org's primary domain). Returns each domain and its type/status.")
SOURCE = "proofpoint"
GROUP = "proofpoint"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "domain": {"type": "string", "description": "the org's primary domain"},
    },
    "required": ["domain"],
    "additionalProperties": False,
}


def run(ctx, domain: str, **_: Any):
    d = (domain or "").strip()
    if not _p.valid_domain(d):
        return {"ok": False, "error": "give a valid domain"}
    data = ctx.client("proofpoint").get(f"/orgs/{d}/domains")
    return _p.rows(data, "domains") or data
