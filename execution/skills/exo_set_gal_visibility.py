"""Hide/unhide a mailbox from the Global Address List (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_set_gal_visibility"
DESCRIPTION = ("Hide a mailbox from (or show it again in) the client's Global Address List / "
               "address book. Pass the mailbox's email address and hidden=true to hide, "
               "hidden=false to unhide. Verifies the change before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "the mailbox's primary email address"},
        "hidden": {"type": "boolean",
                   "description": "true = hide from the address book, false = show"},
    },
    "required": ["identity", "hidden"],
    "additionalProperties": False,
}


def run(ctx, identity: str, hidden: bool, **_: Any):
    from . import _exo_common as c
    r = c.set_and_verify(ctx.client("exo"), identity,
                         {"HiddenFromAddressListsEnabled": bool(hidden)},
                         {"HiddenFromAddressListsEnabled": bool(hidden)},
                         label="set GAL visibility")
    if r.get("ok"):
        r["note"] = ("hidden from the address book" if hidden
                     else "visible in the address book") + " — Outlook may cache the old list"
    return r
