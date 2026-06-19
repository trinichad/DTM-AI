"""Grant a user access to a mailbox — Full Access / Send As / Send on Behalf
(D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_grant_mailbox_access"
DESCRIPTION = ("Grant a user ACCESS to another mailbox (shared or regular). access types: "
               "'full_access' = open/read/manage the mailbox (with Outlook automapping by "
               "default), 'send_as' = send mail AS the mailbox, 'send_on_behalf' = send with "
               "'on behalf of' shown. One access type per call — call again for another. "
               "Verifies the grant before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mailbox": {"type": "string",
                    "description": "the mailbox to grant access TO (its primary address)"},
        "user": {"type": "string",
                 "description": "the user RECEIVING access (their sign-in address)"},
        "access": {"type": "string", "enum": ["full_access", "send_as", "send_on_behalf"],
                   "description": "which right to grant"},
        "automap": {"type": "boolean",
                    "description": "full_access only: auto-open the mailbox in the user's "
                                   "Outlook (default true)"},
    },
    "required": ["mailbox", "user", "access"],
    "additionalProperties": False,
}


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def _holds(rows: list[dict], user: str, who_fields: tuple, right: str) -> bool:
    u = user.lower()
    for row in rows:
        who = any(u == str(row.get(f) or "").lower() for f in who_fields)
        rights = " ".join(str(x) for x in (row.get("AccessRights") or [])).lower()
        if who and right.lower() in rights:
            return True
    return False


def _full_access(c, exo, mailbox: str, user: str, automap: bool):
    list_args = ("Get-MailboxPermission", {"Identity": mailbox})
    who = ("User", "Trustee")
    before = exo.invoke(*list_args)
    if not c.err(before) and _holds(_rows(before), user, who, "FullAccess"):
        return {"ok": True, "note": "the user already has Full Access — nothing to do"}
    r = exo.invoke("Add-MailboxPermission", {"Identity": mailbox, "User": user,
                                             "AccessRights": ["FullAccess"],
                                             "AutoMapping": bool(automap)})
    if c.err(r):
        return {"ok": False, "step": "grant", "error": c.err(r)}
    after = exo.invoke(*list_args)
    if c.err(after) or not _holds(_rows(after), user, who, "FullAccess"):
        return {"ok": False, "step": "verify",
                "error": "the grant returned no error but Full Access is not on the "
                         "permission list — check Exchange directly"}
    return {"ok": True, "note": "Full Access granted"
            + (" — automapped Outlook can take up to ~an hour to show the mailbox"
               if automap else " (no automapping — the user adds the mailbox manually)")}


def _send_as(c, exo, mailbox: str, user: str):
    list_args = ("Get-RecipientPermission", {"Identity": mailbox})
    who = ("Trustee", "User")
    before = exo.invoke(*list_args)
    if not c.err(before) and _holds(_rows(before), user, who, "SendAs"):
        return {"ok": True, "note": "the user already has Send As — nothing to do"}
    r = exo.invoke("Add-RecipientPermission", {"Identity": mailbox, "Trustee": user,
                                               "AccessRights": ["SendAs"], "Confirm": False})
    if c.err(r):
        return {"ok": False, "step": "grant", "error": c.err(r)}
    after = exo.invoke(*list_args)
    if c.err(after) or not _holds(_rows(after), user, who, "SendAs"):
        return {"ok": False, "step": "verify",
                "error": "the grant returned no error but Send As is not on the permission "
                         "list — check Exchange directly"}
    return {"ok": True, "note": "Send As granted — mail sent this way shows only the "
                                "mailbox's address"}


def _send_on_behalf(c, exo, mailbox: str, user: str, mb: dict):
    from ..clients.exo import hashtable
    before = [str(x) for x in (mb.get("GrantSendOnBehalfTo") or [])]
    local = user.split("@")[0].lower()
    if any(user.lower() == b.lower() or local == b.lower() for b in before):
        return {"ok": True, "note": "the user already has Send on Behalf — nothing to do"}
    r = exo.invoke("Set-Mailbox", {"Identity": mailbox, "Confirm": False,
                                   "GrantSendOnBehalfTo": hashtable({"Add": user})})
    if c.err(r):
        return {"ok": False, "step": "grant", "error": c.err(r)}
    after_mb, bad = c.get_one_mailbox(exo, mailbox)
    if bad:
        return {"ok": False, "step": "verify", "error": f"re-read failed — {bad.get('error')}"}
    after = [str(x) for x in (after_mb.get("GrantSendOnBehalfTo") or [])]
    # Exchange stores these as display names, so match loosely: the user (or their alias)
    # appears, or the delegate list grew by the grant.
    grew = len(after) > len(before)
    named = any(user.lower() == a.lower() or local == a.lower() for a in after)
    if not (grew or named):
        return {"ok": False, "step": "verify",
                "error": "the grant returned no error but the Send-on-Behalf list is "
                         "unchanged — check Exchange directly"}
    return {"ok": True, "note": "Send on Behalf granted — recipients see "
                                "'<user> on behalf of <mailbox>'",
            "send_on_behalf_list": after}


def run(ctx, mailbox: str, user: str, access: str, automap: bool = True, **_: Any):
    from . import _exo_common as c
    mailbox, user = (mailbox or "").strip(), (user or "").strip()
    if "@" not in user or " " in user:
        return {"ok": False, "error": f"'{user}' is not a valid user address"}
    exo = ctx.client("exo")
    mb, bad = c.get_one_mailbox(exo, mailbox)
    if bad:
        return bad

    access = (access or "").strip().lower()
    if access == "full_access":
        out = _full_access(c, exo, mailbox, user, automap)
    elif access == "send_as":
        out = _send_as(c, exo, mailbox, user)
    elif access == "send_on_behalf":
        out = _send_on_behalf(c, exo, mailbox, user, mb)
    else:
        return {"ok": False, "error": "access must be full_access, send_as, or send_on_behalf"}
    if out.get("ok"):
        out.update({"mailbox": mb.get("PrimarySmtpAddress") or mailbox, "user": user,
                    "access": access})
    return out
