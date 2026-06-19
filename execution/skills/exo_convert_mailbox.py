"""Convert a mailbox between regular (user) and shared (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_convert_mailbox"
DESCRIPTION = ("Convert a mailbox: a regular USER mailbox to a SHARED mailbox, or a shared "
               "mailbox back to a regular one. Converting to shared frees the license (remove "
               "it afterwards); converting to regular REQUIRES assigning a license within 30 "
               "days or the mailbox is disabled. Verifies the conversion before reporting it.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_TYPES = {"shared": ("Shared", "SharedMailbox"), "regular": ("Regular", "UserMailbox")}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "the mailbox's primary email address"},
        "to": {"type": "string", "enum": ["shared", "regular"],
               "description": "target type: 'shared' or 'regular'"},
    },
    "required": ["identity", "to"],
    "additionalProperties": False,
}


def run(ctx, identity: str, to: str, **_: Any):
    from . import _exo_common as c
    kind = _TYPES.get((to or "").strip().lower())
    if not kind:
        return {"ok": False, "error": "`to` must be 'shared' or 'regular'"}
    set_type, want_details = kind
    exo = ctx.client("exo")
    mb, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return bad
    if str(mb.get("RecipientTypeDetails")) == want_details:
        return {"ok": True, "mailbox": mb.get("PrimarySmtpAddress"),
                "note": f"already a {to} mailbox — nothing to do"}
    r = c.set_and_verify(exo, identity, {"Type": set_type},
                         {"RecipientTypeDetails": want_details}, label="convert mailbox")
    if r.get("ok"):
        r["note"] = ("now SHARED — remove the user's license to free it (shared mailboxes under "
                     "50 GB need none)" if set_type == "Shared"
                     else "now a REGULAR mailbox — assign a license within 30 days or Exchange "
                          "will disable it (use m365_assign_license)")
    return r
