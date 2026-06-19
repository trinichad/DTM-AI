"""Get a Proofpoint Essentials organization's detail (D-86)."""
from __future__ import annotations

from typing import Any

from . import _proofpoint_common as _p

NAME = "proofpoint_get_org"
DESCRIPTION = ("Get a Proofpoint Essentials organization's settings by its primary `domain` "
               "(e.g. acme.com) — name, type, licensing/seats, status, and filtering config.")
SOURCE = "proofpoint"
GROUP = "proofpoint"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "domain": {"type": "string", "description": "the org's primary domain, e.g. acme.com"},
    },
    "required": ["domain"],
    "additionalProperties": False,
}


def run(ctx, domain: str, **_: Any):
    d = (domain or "").strip()
    if not _p.valid_domain(d):
        return {"ok": False, "error": "give a valid domain, e.g. acme.com"}
    return ctx.client("proofpoint").get(f"/orgs/{d}")
