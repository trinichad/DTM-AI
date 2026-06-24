"""Allow a sender for a Proofpoint Essentials user (safe-sender list) (D-86)."""
from __future__ import annotations

from typing import Any

from . import _proofpoint_common as _p

NAME = "proofpoint_allow_sender"
DESCRIPTION = ("Add a sender to a Proofpoint Essentials user's SAFE-sender (allow) list so their "
               "mail is always delivered. Give the org `domain`, the user `email`, and the "
               "`sender` (an email like bob@x.com or a whole domain like x.com). Pass `emails` "
               "(a list) to allow the SAME sender for MANY users in ONE call — do NOT call this "
               "tool once per user. Remove it later with proofpoint_remove_sender.")
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
        "emails": {"type": "array", "items": {"type": "string"},
                   "description": "allow the SAME sender for MANY users in ONE call — a list of "
                                  "user emails in the same org; results come back together. Use "
                                  "this instead of calling the tool once per user."},
        "sender": {"type": "string", "description": "sender email or domain to allow"},
    },
    "required": ["domain", "sender"],
    "additionalProperties": False,
}


def run(ctx, domain: str, email: str = "", emails: Any = None, sender: str = "", **_: Any):
    d, s = (domain or "").strip(), (sender or "").strip()
    if not _p.valid_domain(d):
        return {"ok": False, "error": "give a valid domain"}
    if not _p.valid_sender(s):
        return {"ok": False, "error": "sender must be an email or a domain"}
    wanted = [str(x).strip() for x in (emails or []) if str(x).strip()]
    if wanted:                                         # batch (D-110) — same sender, many users
        results = [_one(ctx, d, e, s) for e in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "users_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, d, email, s)


def _one(ctx, d: str, email: str, s: str) -> dict:
    e = (email or "").strip()
    if not _p.valid_email(e):
        return {"ok": False, "user": e, "error": "give a valid user email"}
    r = _p.mutate_sender(ctx.client("proofpoint"), d, e, s, "safe", add=True)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "user": e, "error": r["error"]}
    return {"ok": True, "user": e, "allowed": s, "note": "added to the safe-sender list"}
