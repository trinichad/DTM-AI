"""WHO has access to a mailbox's CALENDAR or CONTACTS folder (D-97; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_folder_permissions"
DESCRIPTION = ("Show WHO has permissions on a mailbox's CALENDAR (or CONTACTS) folder — every "
               "user and their access level (Owner/Editor/Author/Reviewer/Contributor, or the "
               "calendar-only AvailabilityOnly/LimitedDetails free-busy levels). Includes the "
               "tenant-wide 'Default' (everyone) and 'Anonymous' (external) entries, which set "
               "the baseline free/busy visibility. The reverse of exo_user_folder_access "
               "(that one is per-user across all mailboxes; this one is per-mailbox-folder). "
               "Read-only — pair with exo_grant_folder_access / exo_revoke_folder_access to "
               "change it. Pass `mailboxes` (a list) to check MANY in ONE call — do NOT call "
               "this tool once per mailbox.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
_FOLDERS = {"calendar": "Calendar", "contacts": "Contacts"}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mailbox": {"type": "string",
                    "description": "the mailbox whose folder permissions to list (its email address)"},
        "mailboxes": {"type": "array", "items": {"type": "string"},
                      "description": "act on MANY in ONE call — a list of mailbox addresses; "
                                     "results come back together. Use this instead of calling "
                                     "the tool once per mailbox."},
        "folder": {"type": "string", "enum": list(_FOLDERS),
                   "description": "which folder type to report (default calendar)"},
    },
    "required": [],
    "additionalProperties": False,
}

_WELL_KNOWN = ("default", "anonymous")   # tenant-wide baseline rows, not individual grants


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def run(ctx, mailbox: str = "", folder: str = "calendar", mailboxes: Any = None, **_: Any):
    exo = ctx.client("exo")
    wanted = [str(x).strip() for x in (mailboxes or []) if str(x).strip()]
    if wanted:                                          # batch (D-110) — one call, many mailboxes
        results = ctx.map_progress(wanted[:500], lambda x: _one(exo, x, folder))
        return {"ok": any(r.get("ok") for r in results), "mailboxes_checked": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(exo, mailbox, folder)


def _one(exo, mailbox: str, folder: str = "calendar") -> dict:
    from . import _exo_common as c
    fname = _FOLDERS.get((folder or "calendar").strip().lower())
    if not fname:
        return {"ok": False, "mailbox": mailbox, "error": "folder must be 'calendar' or 'contacts'"}
    mb, bad = c.get_one_mailbox(exo, (mailbox or "").strip())
    if bad:
        return {**bad, "mailbox": mailbox}
    addr = str(mb.get("PrimarySmtpAddress") or mailbox)
    fid = f"{addr}:\\{fname}"

    r = exo.invoke("Get-MailboxFolderPermission", {"Identity": fid})
    if c.err(r):
        e = c.err(r)
        if "couldn't be found" in e.lower():
            return {"ok": False, "mailbox": addr,
                    "error": f"the {fname} folder on '{addr}' was not found — "
                             f"non-English tenants use localized folder names"}
        return {"ok": False, "mailbox": addr, "error": e}

    grants: list[dict] = []
    for row in _rows(r):
        who = str(row.get("User") or "")
        rights = [str(x) for x in (row.get("AccessRights") or [])]
        if not who:
            continue
        grants.append({"user": who, "access": rights,
                       "well_known": who.strip().lower() in _WELL_KNOWN})
    # individual grants first, well-known baseline rows (Default/Anonymous) last
    grants.sort(key=lambda g: (g["well_known"], g["user"].lower()))
    return {"ok": True, "mailbox": addr, "display_name": mb.get("DisplayName"),
            "folder": fname.lower(), "count": len(grants), "permissions": grants}
