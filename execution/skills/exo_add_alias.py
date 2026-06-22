"""Add an email alias (proxy address) to a mailbox (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_add_alias"
DESCRIPTION = ("Add an email ALIAS (additional address) to a mailbox — mail sent to the alias "
               "lands in the same mailbox; the primary address is unchanged. Pass the mailbox's "
               "primary address and the new alias. Verifies the alias before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "the mailbox's primary email address"},
        "alias": {"type": "string", "description": "the alias address to add, "
                                                   "e.g. sales@demodomain.com"},
    },
    "required": ["identity", "alias"],
    "additionalProperties": False,
}


def _aliases(mb: dict) -> list[str]:
    addrs = mb.get("EmailAddresses") or []
    return [str(a) for a in addrs if isinstance(a, str)]


def run(ctx, identity: str, alias: str, **_: Any):
    from ..clients.exo import hashtable
    from . import _exo_common as c
    alias = (alias or "").strip().lower()
    if "@" not in alias or " " in alias:
        return {"ok": False, "error": f"'{alias}' is not a valid email address"}
    exo = ctx.client("exo")
    mb, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return bad
    if any(a.lower() == f"smtp:{alias}" for a in _aliases(mb)):
        return {"ok": True, "mailbox": mb.get("PrimarySmtpAddress"), "alias": alias,
                "note": "that alias is already on the mailbox — nothing to do"}
    guard = c.needs_cloud_management(mb, {"EmailAddresses": True}, label="add the alias")
    if guard:
        return guard

    r = exo.invoke("Set-Mailbox", {"Identity": identity, "Confirm": False,
                                   "EmailAddresses": hashtable({"Add": f"smtp:{alias}"})})
    if c.err(r):
        return {"ok": False, "step": "add alias", "error": c.err(r)}

    after, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return {"ok": False, "step": "verify", "error": f"re-read failed — {bad.get('error')}"}
    if not any(a.lower() == f"smtp:{alias}" for a in _aliases(after)):
        return {"ok": False, "step": "verify",
                "error": f"Set-Mailbox returned no error but '{alias}' is not on the mailbox — "
                         f"check Exchange directly"}
    return {"ok": True, "mailbox": after.get("PrimarySmtpAddress"), "alias_added": alias,
            "all_addresses": _aliases(after)}
