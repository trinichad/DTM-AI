"""Every calendar/contacts folder a USER was granted access to (D-58; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_user_folder_access"
DESCRIPTION = ("Show every CALENDAR or CONTACTS folder a user has been granted access to "
               "across the client's mailboxes (explicit grants only — not the tenant-wide "
               "free/busy default). Checks each mailbox (capped by `limit`). The reverse of "
               "exo_grant_folder_access.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
_FOLDERS = {"calendar": "Calendar", "contacts": "Contacts"}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address"},
        "folder": {"type": "string", "enum": ["calendar", "contacts"],
                   "description": "which folder type to report (default calendar)"},
        "limit": {"type": "integer",
                  "description": "max mailboxes to check (default 100, max 300)"},
    },
    "required": ["user"],
    "additionalProperties": False,
}


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def run(ctx, user: str, folder: str = "calendar", limit: int = 100, **_: Any):
    from . import _exo_common as c
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a sign-in address"}
    fname = _FOLDERS.get((folder or "calendar").strip().lower())
    if not fname:
        return {"ok": False, "error": "folder must be 'calendar' or 'contacts'"}
    limit = max(1, min(int(limit or 100), 300))
    exo = ctx.client("exo")

    r = exo.invoke("Get-Mailbox", {"ResultSize": limit})
    if c.err(r):
        return {"ok": False, "error": c.err(r)}
    boxes = _rows(r)
    grants: list[dict] = []
    for mb in boxes:
        addr = str(mb.get("PrimarySmtpAddress") or "")
        if not addr or addr.lower() == user.lower():
            continue
        p = exo.invoke("Get-MailboxFolderPermission",
                       {"Identity": f"{addr}:\\{fname}", "User": user})
        if c.err(p):                                   # no grant for this user → an error row
            continue
        for row in _rows(p):
            rights = [str(x) for x in (row.get("AccessRights") or [])]
            if rights:
                grants.append({"mailbox": addr, "display_name": mb.get("DisplayName"),
                               "access": rights})
    out: dict[str, Any] = {"ok": True, "user": user, "folder": fname.lower(),
                           "count": len(grants), "grants": grants,
                           "mailboxes_checked": len(boxes)}
    if len(boxes) >= limit:
        out["note"] = f"checked the first {limit} mailboxes — raise `limit` (max 300) for more"
    return out
