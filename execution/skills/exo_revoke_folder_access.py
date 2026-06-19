"""Remove a user's access to another user's Calendar or Contacts
(D-63; SOP: exchange-online). The mirror of exo_grant_folder_access."""
from __future__ import annotations

from typing import Any

NAME = "exo_revoke_folder_access"
DESCRIPTION = ("REMOVE a user's access to another user's CALENDAR or CONTACTS folder (an "
               "explicit grant — the tenant-wide free/busy default is unaffected). See "
               "grants with exo_user_folder_access. Verifies the permission is gone before "
               "reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_FOLDERS = {"calendar": "Calendar", "contacts": "Contacts"}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mailbox": {"type": "string",
                    "description": "whose folder it is (their email address)"},
        "user": {"type": "string", "description": "who LOSES access (their email address)"},
        "folder": {"type": "string", "enum": list(_FOLDERS), "description": "which folder"},
    },
    "required": ["mailbox", "user", "folder"],
    "additionalProperties": False,
}


def run(ctx, mailbox: str, user: str, folder: str, **_: Any):
    from . import _exo_common as c
    from .exo_grant_folder_access import _rows, _user_entry
    mailbox, user = (mailbox or "").strip(), (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a valid user address"}
    fname = _FOLDERS.get((folder or "").strip().lower())
    if not fname:
        return {"ok": False, "error": "folder must be calendar or contacts"}
    exo = ctx.client("exo")
    mb, bad = c.get_one_mailbox(exo, mailbox)
    if bad:
        return bad
    fid = f"{mailbox}:\\{fname}"

    cur = exo.invoke("Get-MailboxFolderPermission", {"Identity": fid})
    held = _user_entry(_rows(cur), user) if not c.err(cur) else None
    if not held:
        return {"ok": True, "mailbox": mailbox, "user": user, "folder": fname.lower(),
                "note": "the user has no explicit grant on that folder — nothing to remove"}

    r = exo.invoke("Remove-MailboxFolderPermission", {"Identity": fid, "User": user,
                                                      "Confirm": False})
    if c.err(r):
        return {"ok": False, "step": "revoke", "error": c.err(r)}

    check = exo.invoke("Get-MailboxFolderPermission", {"Identity": fid})
    if not c.err(check) and _user_entry(_rows(check), user):
        return {"ok": False, "step": "verify",
                "error": f"the revoke returned no error but {user} still has rights on "
                         f"{fname} — check Exchange directly"}
    return {"ok": True, "mailbox": mailbox, "user": user, "folder": fname.lower(),
            "access_revoked": str(" ".join(str(x) for x in (held.get("AccessRights") or []))),
            "note": "removed — the user's Outlook may need a restart to drop the folder"}
