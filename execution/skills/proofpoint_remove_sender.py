"""Remove a sender from a Proofpoint Essentials user's safe/blocked list (D-86).

The opposite of proofpoint_allow_sender / proofpoint_block_sender.
"""
from __future__ import annotations

from typing import Any

from . import _proofpoint_common as _p

NAME = "proofpoint_remove_sender"
DESCRIPTION = ("Remove a sender from a Proofpoint Essentials user's safe OR blocked list. Give the "
               "org `domain`, the user `email`, the `sender`, and `list` ('safe' or 'blocked').")
SOURCE = "proofpoint"
GROUP = "proofpoint"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_LISTS = ("safe", "blocked")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "domain": {"type": "string", "description": "the org's primary domain"},
        "email": {"type": "string", "description": "the user's email"},
        "sender": {"type": "string", "description": "sender email or domain to remove"},
        "list": {"type": "string", "enum": list(_LISTS), "description": "'safe' or 'blocked'"},
    },
    "required": ["domain", "email", "sender", "list"],
    "additionalProperties": False,
}


def run(ctx, domain: str, email: str, sender: str, list: str, **_: Any):
    d, e, s = (domain or "").strip(), (email or "").strip(), (sender or "").strip()
    which = (list or "").strip().lower()
    if not _p.valid_domain(d):
        return {"ok": False, "error": "give a valid domain"}
    if not _p.valid_email(e):
        return {"ok": False, "error": "give a valid user email"}
    if not _p.valid_sender(s):
        return {"ok": False, "error": "sender must be an email or a domain"}
    if which not in _LISTS:
        return {"ok": False, "error": "list must be 'safe' or 'blocked'"}
    r = _p.mutate_sender(ctx.client("proofpoint"), d, e, s, which, add=False)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "user": e, "removed": s, "list": which, "note": "removed from the list"}
