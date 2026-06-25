"""Revoke a user's access to a mailbox — Full Access / Send As / Send on Behalf
(D-63; SOP: exchange-online). The mirror of exo_grant_mailbox_access."""
from __future__ import annotations

from typing import Any

NAME = "exo_revoke_mailbox_access"
DESCRIPTION = ("REMOVE a user's access to a mailbox (shared or regular): 'full_access', "
               "'send_as', or 'send_on_behalf'. One access type per call — call again for "
               "another. See who has access with exo_mailbox_permissions. Revoking destroys "
               "no data and can be undone by re-granting (exo_grant_mailbox_access). Revoke the "
               "SAME access from MANY users in ONE call by passing `users` (a list) instead of "
               "`user` — do NOT call this tool once per user. Verifies the permission is gone "
               "before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mailbox": {"type": "string",
                    "description": "the mailbox to remove access FROM (its primary address)"},
        "user": {"type": "string",
                 "description": "the user LOSING access (their sign-in address)"},
        "users": {"type": "array", "items": {"type": "string"},
                  "description": "revoke the SAME access from MANY users in ONE call — a list of "
                                 "sign-in addresses; results come back together. Use this instead "
                                 "of calling the tool once per user."},
        "access": {"type": "string", "enum": ["full_access", "send_as", "send_on_behalf"],
                   "description": "which right to revoke"},
    },
    "required": ["mailbox", "access"],
    "additionalProperties": False,
}


def _full_access(c, h, exo, mailbox: str, user: str):
    list_args = ("Get-MailboxPermission", {"Identity": mailbox})
    who = ("User", "Trustee")
    before = exo.invoke(*list_args)
    if not c.err(before) and not h(_rows(before), user, who, "FullAccess"):
        return {"ok": True, "note": "the user doesn't have Full Access — nothing to remove"}
    r = exo.invoke("Remove-MailboxPermission", {"Identity": mailbox, "User": user,
                                                "AccessRights": ["FullAccess"],
                                                "Confirm": False})
    if c.err(r):
        return {"ok": False, "step": "revoke", "error": c.err(r)}
    after = exo.invoke(*list_args)
    if c.err(after) or h(_rows(after), user, who, "FullAccess"):
        return {"ok": False, "step": "verify",
                "error": "the revoke returned no error but Full Access is still on the "
                         "permission list — check Exchange directly"}
    return {"ok": True, "note": "Full Access removed — an automapped mailbox disappears from "
                                "the user's Outlook within ~an hour"}


def _send_as(c, h, exo, mailbox: str, user: str):
    list_args = ("Get-RecipientPermission", {"Identity": mailbox})
    who = ("Trustee", "User")
    before = exo.invoke(*list_args)
    if not c.err(before) and not h(_rows(before), user, who, "SendAs"):
        return {"ok": True, "note": "the user doesn't have Send As — nothing to remove"}
    r = exo.invoke("Remove-RecipientPermission", {"Identity": mailbox, "Trustee": user,
                                                  "AccessRights": ["SendAs"],
                                                  "Confirm": False})
    if c.err(r):
        return {"ok": False, "step": "revoke", "error": c.err(r)}
    after = exo.invoke(*list_args)
    if c.err(after) or h(_rows(after), user, who, "SendAs"):
        return {"ok": False, "step": "verify",
                "error": "the revoke returned no error but Send As is still on the "
                         "permission list — check Exchange directly"}
    return {"ok": True, "note": "Send As removed"}


def _send_on_behalf(c, exo, mailbox: str, user: str, mb: dict):
    from ..clients.exo import hashtable
    before = [str(x) for x in (mb.get("GrantSendOnBehalfTo") or [])]
    local = user.split("@")[0].lower()
    held = any(user.lower() == b.lower() or local == b.lower() for b in before)
    if not before:
        return {"ok": True, "note": "the user doesn't have Send on Behalf — nothing to remove"}
    # if the list is non-empty but stored as display names, `held` may be False even though
    # the user IS a delegate — attempt the remove and let the verify pass judge it
    r = exo.invoke("Set-Mailbox", {"Identity": mailbox, "Confirm": False,
                                   "GrantSendOnBehalfTo": hashtable({"Remove": user})})
    if c.err(r):
        return {"ok": False, "step": "revoke", "error": c.err(r)}
    after_mb, bad = c.get_one_mailbox(exo, mailbox)
    if bad:
        return {"ok": False, "step": "verify", "error": f"re-read failed — {bad.get('error')}"}
    after = [str(x) for x in (after_mb.get("GrantSendOnBehalfTo") or [])]
    # Exchange stores delegates as display names — verify by the list shrinking or the
    # user no longer matching (same loose match as the grant side).
    shrank = len(after) < len(before)
    named = any(user.lower() == a.lower() or local == a.lower() for a in after)
    if named or (held and not shrank):
        return {"ok": False, "step": "verify",
                "error": "the revoke returned no error but the Send-on-Behalf list is "
                         "unchanged — check Exchange directly",
                "send_on_behalf_list": after}
    return {"ok": True, "note": "Send on Behalf removed", "send_on_behalf_list": after}


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def run(ctx, mailbox: str, user: str = "", access: str = "", users: Any = None, **_: Any):
    from . import _exo_common as c
    mailbox = (mailbox or "").strip()
    access = (access or "").strip().lower()
    if access not in ("full_access", "send_as", "send_on_behalf"):
        return {"ok": False, "error": "access must be full_access, send_as, or send_on_behalf"}
    exo = ctx.client("exo")
    mb, bad = c.get_one_mailbox(exo, mailbox)              # shared preflight — once for the batch
    if bad:
        return bad

    recipients = [u for u in (str(x).strip() for x in (users or [])) if u]
    if recipients:                                     # batch revoke (D-110) — ONE call, ONE approval
        results = ctx.map_progress(recipients[:500],
                                   lambda u: _one(c, exo, mailbox, mb, u, access))
        return {"ok": any(r.get("ok") for r in results),
                "mailbox": mb.get("PrimarySmtpAddress") or mailbox, "access": access,
                "users_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(c, exo, mailbox, mb, user, access)


def _one(c, exo, mailbox: str, mb: dict, user: str, access: str) -> dict:
    from .exo_grant_mailbox_access import _holds as h
    user = (user or "").strip()
    if "@" not in user or " " in user:
        return {"ok": False, "user": user, "error": f"'{user}' is not a valid user address"}

    if access == "full_access":
        out = _full_access(c, h, exo, mailbox, user)
    elif access == "send_as":
        out = _send_as(c, h, exo, mailbox, user)
    else:
        out = _send_on_behalf(c, exo, mailbox, user, mb)
    out.update({"user": user, "access_revoked": access} if out.get("ok") else {"user": user})
    if out.get("ok"):
        out["mailbox"] = mb.get("PrimarySmtpAddress") or mailbox
    return out
