"""Rename a (shared) mailbox's address to zzz_<old> so mail to the old address BOUNCES
(D-105; SOP: exchange-online). Split out of m365_offboard_user so it's a deliberate manual step."""
from __future__ import annotations

from typing import Any

NAME = "exo_rename_smtp"
DESCRIPTION = ("Rename a mailbox's primary email to zzz_<old>@domain and DROP the old address, so "
               "new mail to the original address BOUNCES instead of silently collecting in a "
               "shared mailbox. Typically the last offboarding step, run once a manager confirms "
               "no mail flow is still needed. Verifies the old address is gone before reporting "
               "success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the mailbox's current email address"},
    },
    "required": ["user"],
    "additionalProperties": False,
}


def run(ctx, user: str, **_: Any):
    from ..clients.exo import hashtable
    from . import _exo_common as c
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a valid email address"}
    exo = ctx.client("exo")
    mb, bad = c.get_one_mailbox(exo, user)
    if bad:
        return bad

    local, domain = user.split("@", 1)
    if local.startswith("zzz_"):
        return {"ok": True, "mailbox": user,
                "note": "already renamed (zzz_ prefix) — nothing to do"}
    new_address = f"zzz_{local}@{domain}"

    r = exo.invoke("Set-Mailbox", {"Identity": user, "Confirm": False,
                                   "WindowsEmailAddress": new_address,
                                   "MicrosoftOnlineServicesID": new_address})
    if c.err(r):
        # surfaces the D-91 cloud-management hint when the mailbox is AD-synced and not cloud-managed
        guard = c.needs_cloud_management(mb, {"WindowsEmailAddress": new_address},
                                         label="rename the email")
        return guard or {"ok": False, "step": "rename", "error": c.err(r)}

    # drop the OLD address as a proxy so mail to it bounces
    r2 = exo.invoke("Set-Mailbox", {"Identity": new_address, "Confirm": False,
                                    "EmailAddresses": hashtable({"Remove": f"smtp:{user}"})})
    after, _ = c.get_one_mailbox(exo, new_address)
    old_gone = after is not None and not any(
        str(a).lower() == f"smtp:{user.lower()}" for a in (after.get("EmailAddresses") or []))
    if c.err(r2) or not old_gone:
        return {"ok": False, "step": "verify", "mailbox": new_address,
                "error": (f"renamed to {new_address} but the OLD address may still be attached — "
                          f"mail might not bounce; check Exchange "
                          f"({c.err(r2) or 'old alias still listed'})")}
    return {"ok": True, "mailbox": new_address, "renamed_from": user,
            "note": f"now {new_address}; mail to {user} bounces"}
