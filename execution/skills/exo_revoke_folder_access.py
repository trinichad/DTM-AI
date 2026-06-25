"""Remove a user's access to another user's Calendar or Contacts
(D-63; SOP: exchange-online). The mirror of exo_grant_folder_access."""
from __future__ import annotations

from typing import Any

NAME = "exo_revoke_folder_access"
DESCRIPTION = ("REMOVE a user's access to another user's CALENDAR or CONTACTS folder (an "
               "explicit grant — the tenant-wide free/busy default is unaffected). See "
               "grants with exo_user_folder_access. Revoke the SAME folder from MANY users in ONE "
               "call by passing `users` (a list) instead of `user` — do NOT call this tool once "
               "per user. Verifies the permission is gone before reporting success.")
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
        "users": {"type": "array", "items": {"type": "string"},
                  "description": "revoke the SAME folder from MANY users in ONE call — a list of "
                                 "email addresses; results come back together. Use this instead "
                                 "of calling the tool once per user."},
        "folder": {"type": "string", "enum": list(_FOLDERS), "description": "which folder"},
    },
    "required": ["mailbox", "folder"],
    "additionalProperties": False,
}


def run(ctx, mailbox: str, user: str = "", folder: str = "", users: Any = None, **_: Any):
    from . import _exo_common as c
    mailbox = (mailbox or "").strip()
    fname = _FOLDERS.get((folder or "").strip().lower())
    if not fname:
        return {"ok": False, "error": "folder must be calendar or contacts"}
    exo = ctx.client("exo")
    mb, bad = c.get_one_mailbox(exo, mailbox)              # shared preflight — once for the batch
    if bad:
        return bad
    fid = f"{mailbox}:\\{fname}"

    wanted = [u for u in (str(x).strip() for x in (users or [])) if u]
    if wanted:                                         # batch revoke (D-110) — ONE call, ONE approval
        results = ctx.map_progress(wanted[:500], lambda u: _one(exo, mailbox, fid, fname, u))
        return {"ok": any(r.get("ok") for r in results), "mailbox": mailbox,
                "folder": fname.lower(), "users_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(exo, mailbox, fid, fname, user)


def _one(exo, mailbox: str, fid: str, fname: str, user: str) -> dict:
    from . import _exo_common as c
    from .exo_grant_folder_access import _rows, _user_entry, identifiers
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "user": user, "error": f"'{user}' is not a valid user address"}
    idents = identifiers(exo, user)

    cur = exo.invoke("Get-MailboxFolderPermission", {"Identity": fid})
    held = _user_entry(_rows(cur), user, idents) if not c.err(cur) else None
    if not held:
        return {"ok": True, "mailbox": mailbox, "user": user, "folder": fname.lower(),
                "note": "the user has no explicit grant on that folder — nothing to remove"}

    r = exo.invoke("Remove-MailboxFolderPermission", {"Identity": fid, "User": user,
                                                      "Confirm": False})
    if c.err(r):
        return {"ok": False, "user": user, "step": "revoke", "error": c.err(r)}

    check = exo.invoke("Get-MailboxFolderPermission", {"Identity": fid})
    if not c.err(check) and _user_entry(_rows(check), user, idents):
        return {"ok": False, "user": user, "step": "verify",
                "error": f"the revoke returned no error but {user} still has rights on "
                         f"{fname} — check Exchange directly"}
    return {"ok": True, "mailbox": mailbox, "user": user, "folder": fname.lower(),
            "access_revoked": str(" ".join(str(x) for x in (held.get("AccessRights") or []))),
            "note": "removed — the user's Outlook may need a restart to drop the folder"}
