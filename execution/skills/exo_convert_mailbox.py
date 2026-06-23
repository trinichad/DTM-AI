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


# Mailbox-type conversion is eventually-consistent: Set-Mailbox -Type accepts the change but
# Get-Mailbox can keep reporting the OLD RecipientTypeDetails for several seconds (sometimes a
# minute or two) before it flips. A single immediate re-read therefore falsely reported "the
# change did not stick" (D-99). Poll the re-read instead of verifying once.
_POLL_ATTEMPTS = 6
_POLL_DELAY_S = 2.0


def run(ctx, identity: str, to: str, **_: Any):
    import time

    from . import _exo_common as c
    kind = _TYPES.get((to or "").strip().lower())
    if not kind:
        return {"ok": False, "error": "`to` must be 'shared' or 'regular'"}
    set_type, want_details = kind
    exo = ctx.client("exo")
    mb, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return bad
    addr = mb.get("PrimarySmtpAddress") or identity
    if str(mb.get("RecipientTypeDetails")) == want_details:
        return {"ok": True, "mailbox": addr,
                "note": f"already a {to} mailbox — nothing to do"}

    r = exo.invoke("Set-Mailbox", {"Identity": identity, "Type": set_type, "Confirm": False})
    if c.err(r):
        return {"ok": False, "step": "convert", "error": c.err(r)}

    # Poll until the new type shows up (don't declare failure on the first stale read — D-99).
    after = mb
    for attempt in range(_POLL_ATTEMPTS):
        time.sleep(_POLL_DELAY_S)
        nxt, bad = c.get_one_mailbox(exo, identity)
        if nxt:
            after = nxt
            if str(after.get("RecipientTypeDetails")) == want_details:
                note = ("now SHARED — remove the user's license to free it (shared mailboxes "
                        "under 50 GB need none)" if set_type == "Shared"
                        else "now a REGULAR mailbox — assign a license within 30 days or Exchange "
                             "will disable it (use m365_assign_license)")
                return {"ok": True, "mailbox": addr, "to": to,
                        "recipient_type": after.get("RecipientTypeDetails"), "note": note}

    # Set-Mailbox returned no error but the type hasn't flipped within the poll window. This is
    # almost always propagation lag, not a failure — say so, and don't imply the convert was rejected.
    return {"ok": False, "step": "verify", "pending": True, "mailbox": addr,
            "recipient_type": after.get("RecipientTypeDetails"),
            "error": (f"Exchange accepted the conversion of {addr} to {to}, but it still shows "
                      f"'{after.get('RecipientTypeDetails')}' after "
                      f"~{int(_POLL_ATTEMPTS * _POLL_DELAY_S)}s. Mailbox-type conversions can take "
                      f"a few minutes to propagate — re-check with exo_mailbox_details shortly; do "
                      f"NOT re-run the convert (it likely already took).")}
