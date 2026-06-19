"""Change a mailbox's primary SMTP address (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_set_primary_smtp"
DESCRIPTION = ("Change a mailbox's PRIMARY email (SMTP) address. The old address is kept as an "
               "alias automatically, so old mail still arrives. By default the sign-in User ID "
               "is updated to match the new address (set update_signin=false to leave it). "
               "Verifies the change before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "the mailbox's CURRENT primary address"},
        "new_address": {"type": "string", "description": "the NEW primary SMTP address"},
        "update_signin": {"type": "boolean",
                          "description": "also change the sign-in User ID to the new address "
                                         "(default true — keeps User ID and email in lockstep)"},
    },
    "required": ["identity", "new_address"],
    "additionalProperties": False,
}


def run(ctx, identity: str, new_address: str, update_signin: bool = True, **_: Any):
    from . import _exo_common as c
    new_address = (new_address or "").strip()
    if "@" not in new_address or " " in new_address:
        return {"ok": False, "error": f"'{new_address}' is not a valid email address"}
    params: dict[str, Any] = {"WindowsEmailAddress": new_address}
    if update_signin:
        params["MicrosoftOnlineServicesID"] = new_address
    r = c.set_and_verify(ctx.client("exo"), identity, params,
                         {"PrimarySmtpAddress": new_address}, label="set primary SMTP")
    if r.get("ok"):
        r["note"] = ("the old address remains as an alias"
                     + ("; the sign-in User ID was updated to the new address — the user signs "
                        "in with it from now on" if update_signin else ""))
    return r
