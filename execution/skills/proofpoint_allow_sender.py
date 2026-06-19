"""Allow a sender for a Proofpoint Essentials user (safe-sender list) (D-86)."""
from __future__ import annotations

from typing import Any

from . import _proofpoint_common as _p

NAME = "proofpoint_allow_sender"
DESCRIPTION = ("Add a sender to a Proofpoint Essentials user's SAFE-sender (allow) list so their "
               "mail is always delivered. Give the org `domain`, the user `email`, and the "
               "`sender` (an email like bob@x.com or a whole domain like x.com). Remove it later "
               "with proofpoint_remove_sender.")
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
        "sender": {"type": "string", "description": "sender email or domain to allow"},
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
    r = _p.mutate_sender(ctx.client("proofpoint"), d, e, s, "safe", add=True)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "user": e, "allowed": s, "note": "added to the safe-sender list"}
