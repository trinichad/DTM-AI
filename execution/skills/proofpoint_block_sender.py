"""Block a sender for a Proofpoint Essentials user (blocked-sender list) (D-86)."""
from __future__ import annotations

from typing import Any

from . import _proofpoint_common as _p

NAME = "proofpoint_block_sender"
DESCRIPTION = ("Add a sender to a Proofpoint Essentials user's BLOCKED-sender list so their mail is "
               "always blocked. Give the org `domain`, the user `email`, and the `sender` (email "
               "or domain). Remove it later with proofpoint_remove_sender.")
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
        "sender": {"type": "string", "description": "sender email or domain to block"},
    },
    "required": ["domain", "email", "sender"],
    "additionalProperties": False,
}


def run(ctx, domain: str, email: str, sender: str, **_: Any):
    d, e, s = (domain or "").strip(), (email or "").strip(), (sender or "").strip()
    if not _p.valid_domain(d):
        return {"ok": False, "error": "give a valid domain"}
    if not _p.valid_email(e):
        return {"ok": False, "error": "give a valid user email"}
    if not _p.valid_sender(s):
        return {"ok": False, "error": "sender must be an email or a domain"}
    r = _p.mutate_sender(ctx.client("proofpoint"), d, e, s, "blocked", add=True)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "user": e, "blocked": s, "note": "added to the blocked-sender list"}
