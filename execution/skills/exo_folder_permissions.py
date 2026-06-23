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
               "Read-only — pair with exo_grant_folder_access / exo_revoke_folder_access to change it.")
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
        "folder": {"type": "string", "enum": list(_FOLDERS),
                   "description": "which folder type to report (default calendar)"},
    },
    "required": ["mailbox"],
    "additionalProperties": False,
}

_WELL_KNOWN = ("default", "anonymous")   # tenant-wide baseline rows, not individual grants


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def run(ctx, mailbox: str, folder: str = "calendar", **_: Any):
    from . import _exo_common as c
    fname = _FOLDERS.get((folder or "calendar").strip().lower())
    if not fname:
        return {"ok": False, "error": "folder must be 'calendar' or 'contacts'"}
    exo = ctx.client("exo")
    mb, bad = c.get_one_mailbox(exo, (mailbox or "").strip())
    if bad:
        return bad
    addr = str(mb.get("PrimarySmtpAddress") or mailbox)
    fid = f"{addr}:\\{fname}"

    r = exo.invoke("Get-MailboxFolderPermission", {"Identity": fid})
    if c.err(r):
        e = c.err(r)
        if "couldn't be found" in e.lower():
            return {"ok": False, "error": f"the {fname} folder on '{addr}' was not found — "
                                          f"non-English tenants use localized folder names"}
        return {"ok": False, "error": e}

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
