"""Grant a user access to another user's Calendar or Contacts (D-58; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_grant_folder_access"
DESCRIPTION = ("Grant a user access to another user's CALENDAR or CONTACTS folder. access "
               "levels: 'reviewer' = read only, 'editor' = read/write items, 'author' = "
               "read + add, 'owner' = full control, 'contributor' = add only; calendar-only: "
               "'availability_only' = free/busy, 'limited_details' = free/busy + subject. "
               "Updates the level if the user already has one. Verifies before reporting "
               "success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_FOLDERS = {"calendar": "Calendar", "contacts": "Contacts"}
_RIGHTS = {"owner": "Owner", "editor": "Editor", "author": "Author", "reviewer": "Reviewer",
           "contributor": "Contributor", "availability_only": "AvailabilityOnly",
           "limited_details": "LimitedDetails"}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mailbox": {"type": "string",
                    "description": "whose folder is being shared (their email address)"},
        "user": {"type": "string", "description": "who RECEIVES access (their email address)"},
        "folder": {"type": "string", "enum": list(_FOLDERS), "description": "which folder"},
        "access": {"type": "string", "enum": list(_RIGHTS),
                   "description": "the access level to grant"},
    },
    "required": ["mailbox", "user", "folder", "access"],
    "additionalProperties": False,
}


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def identifiers(exo, user: str) -> set[str]:
    """Every lowercased string a Get-MailboxFolderPermission `User` row might show for `user`.
    Exchange echoes the resolved DISPLAY NAME (e.g. "Susan Grosso"), not the address you passed —
    so matching on the email alone silently misses every existing entry (D-98). Resolve the
    mailbox once and collect its display name / alias / name / UPN too."""
    from . import _exo_common as c
    idents = {user.strip().lower(), user.split("@")[0].strip().lower()}
    mb = exo.invoke("Get-Mailbox", {"Identity": user})
    rows = _rows(mb)
    if not c.err(mb) and len(rows) == 1:
        for k in ("DisplayName", "Alias", "Name", "PrimarySmtpAddress", "UserPrincipalName"):
            v = str(rows[0].get(k) or "").strip().lower()
            if v:
                idents.add(v)
    return {i for i in idents if i}


def _user_entry(rows: list[dict], user: str, idents: set[str] | None = None) -> dict | None:
    pool = idents if idents is not None else {user.strip().lower(), user.split("@")[0].strip().lower()}
    for row in rows:
        who = str(row.get("User") or "").strip().lower()
        if who in pool:
            return row
    return None


def run(ctx, mailbox: str, user: str, folder: str, access: str, **_: Any):
    from . import _exo_common as c
    mailbox, user = (mailbox or "").strip(), (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a valid user address"}
    fname = _FOLDERS.get((folder or "").strip().lower())
    right = _RIGHTS.get((access or "").strip().lower())
    if not fname or not right:
        return {"ok": False, "error": "folder must be calendar/contacts; access one of: "
                                      + ", ".join(_RIGHTS)}
    if right in ("AvailabilityOnly", "LimitedDetails") and fname != "Calendar":
        return {"ok": False, "error": f"'{access}' applies to calendars only"}
    exo = ctx.client("exo")
    mb, bad = c.get_one_mailbox(exo, mailbox)
    if bad:
        return bad
    fid = f"{mailbox}:\\{fname}"
    idents = identifiers(exo, user)

    cur = exo.invoke("Get-MailboxFolderPermission", {"Identity": fid})
    if c.err(cur) and "couldn't be found" in c.err(cur).lower():
        return {"ok": False, "error": f"the {fname} folder on '{mailbox}' was not found — "
                                      f"non-English tenants use localized folder names"}
    held = _user_entry(_rows(cur), user, idents) if not c.err(cur) else None
    held_rights = " ".join(str(x) for x in (held.get("AccessRights") or [])) if held else ""
    if held and right.lower() in held_rights.lower():
        return {"ok": True, "mailbox": mailbox, "user": user, "folder": fname.lower(),
                "access": access, "note": "the user already has that access — nothing to do"}

    args = {"Identity": fid, "User": user, "AccessRights": [right], "Confirm": False}
    cmdlet = "Set-MailboxFolderPermission" if held else "Add-MailboxFolderPermission"
    r = exo.invoke(cmdlet, args)
    # Self-heal: if we chose Add- but Exchange says the user already has an entry (an earlier
    # grant our display-name match missed — D-98), switch to Set- instead of failing.
    if c.err(r) and cmdlet == "Add-MailboxFolderPermission" \
            and "alreadyexist" in c.err(r).replace(" ", "").lower():
        cmdlet = "Set-MailboxFolderPermission"
        r = exo.invoke(cmdlet, args)
        held = held or {"AccessRights": ["(existing)"]}     # it WAS already present
    if c.err(r):
        return {"ok": False, "step": "grant", "error": c.err(r)}

    check = exo.invoke("Get-MailboxFolderPermission", {"Identity": fid})
    now = _user_entry(_rows(check), user, idents) if not c.err(check) else None
    now_rights = " ".join(str(x) for x in (now.get("AccessRights") or [])) if now else ""
    if right.lower() not in now_rights.lower():
        return {"ok": False, "step": "verify",
                "error": f"{cmdlet} returned no error but the {fname} permission doesn't "
                         f"show '{right}' for {user} — check Exchange directly"}
    return {"ok": True, "mailbox": mailbox, "user": user, "folder": fname.lower(),
            "access_granted": access,
            **({"replaced": held_rights} if held_rights else {}),
            "note": f"{user} now has {right} on {mailbox}'s {fname.lower()} — Outlook may "
                    f"need a restart to show it"}
