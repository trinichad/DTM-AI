"""Every mailbox a USER can access (the reverse lookup) (D-58; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_user_mailbox_access"
DESCRIPTION = ("Show every MAILBOX a user has access to — Full Access, Send As, or Send on "
               "Behalf. Checks each mailbox in the client (capped by `limit`; raise it for "
               "big tenants). The reverse of exo_mailbox_permissions.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address"},
        "limit": {"type": "integer",
                  "description": "max mailboxes to check (default 100, max 300 — one pass "
                                 "per mailbox)"},
    },
    "required": ["user"],
    "additionalProperties": False,
}


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def run(ctx, user: str, limit: int = 100, **_: Any):
    from . import _exo_common as c
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a sign-in address"}
    limit = max(1, min(int(limit or 100), 300))
    local = user.split("@")[0].lower()
    exo = ctx.client("exo")

    r = exo.invoke("Get-Mailbox", {"ResultSize": limit})
    if c.err(r):
        return {"ok": False, "error": c.err(r)}
    boxes = _rows(r)
    access: list[dict] = []
    for mb in boxes:
        addr = str(mb.get("PrimarySmtpAddress") or "")
        if not addr or addr.lower() == user.lower():
            continue                                   # their own mailbox isn't "access"
        rights = []
        fa = exo.invoke("Get-MailboxPermission", {"Identity": addr, "User": user})
        if not c.err(fa) and any("fullaccess" in " ".join(
                str(x) for x in (row.get("AccessRights") or [])).lower()
                for row in _rows(fa)):
            rights.append("full_access")
        sa = exo.invoke("Get-RecipientPermission", {"Identity": addr, "Trustee": user})
        if not c.err(sa) and _rows(sa):
            rights.append("send_as")
        sob = [str(x).lower() for x in (mb.get("GrantSendOnBehalfTo") or [])]
        if any(user.lower() == s or local == s for s in sob):
            rights.append("send_on_behalf")
        if rights:
            access.append({"mailbox": addr, "display_name": mb.get("DisplayName"),
                           "type": mb.get("RecipientTypeDetails"), "access": rights})
    out: dict[str, Any] = {"ok": True, "user": user, "count": len(access),
                           "mailboxes": access, "mailboxes_checked": len(boxes)}
    if len(boxes) >= limit:
        out["note"] = (f"checked the first {limit} mailboxes — raise `limit` (max 300) to "
                       f"sweep more")
    if not access:
        out["note"] = out.get("note", "") + (" " if out.get("note") else "") + \
            f"{user} has no extra mailbox access in what was checked"
    return out
