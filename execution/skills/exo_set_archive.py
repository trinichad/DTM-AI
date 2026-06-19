"""Enable/disable a mailbox's ONLINE ARCHIVE (D-55; SOP: exchange-online).

The connector FORCES Archive:true onto Enable-/Disable-Mailbox (FORCED_PARAMS), so this
path can only ever toggle the archive — never mailbox-disable an account.
"""
from __future__ import annotations

from typing import Any

NAME = "exo_set_archive"
DESCRIPTION = ("Enable or disable a mailbox's ONLINE (In-Place) ARCHIVE. Enabling needs a "
               "license that includes archiving (e.g. Exchange Online Plan 2 / E3+). DISABLING "
               "disconnects the archive — its contents are kept ~30 days, then purged. "
               "Verifies the state before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "the mailbox's primary email address"},
        "enabled": {"type": "boolean", "description": "true = enable the archive, false = disable"},
    },
    "required": ["identity", "enabled"],
    "additionalProperties": False,
}

_NO_ARCHIVE_GUID = "00000000-0000-0000-0000-000000000000"


def _has_archive(mb: dict) -> bool:
    guid = str(mb.get("ArchiveGuid") or "")
    return bool(guid) and guid != _NO_ARCHIVE_GUID and str(mb.get("ArchiveState")) != "None"


def run(ctx, identity: str, enabled: bool, **_: Any):
    from . import _exo_common as c
    exo = ctx.client("exo")
    mb, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return bad
    if _has_archive(mb) == bool(enabled):
        return {"ok": True, "mailbox": mb.get("PrimarySmtpAddress"),
                "note": f"archive is already {'enabled' if enabled else 'disabled'} — nothing to do"}

    cmdlet = "Enable-Mailbox" if enabled else "Disable-Mailbox"
    r = exo.invoke(cmdlet, {"Identity": identity, "Confirm": False})
    if c.err(r):
        e = c.err(r)
        hint = (" (the license may not include archiving — needs Exchange Online Plan 2 / E3+)"
                if enabled and ("license" in e.lower() or "plan" in e.lower()) else "")
        return {"ok": False, "step": cmdlet, "error": e + hint}

    after, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return {"ok": False, "step": "verify", "error": f"re-read failed — {bad.get('error')}"}
    if _has_archive(after) != bool(enabled):
        return {"ok": False, "step": "verify",
                "error": f"{cmdlet} returned no error but the archive state didn't change — "
                         f"check Exchange directly (provisioning can lag a few minutes)"}
    return {"ok": True, "mailbox": after.get("PrimarySmtpAddress"),
            "archive": "enabled" if enabled else "disabled",
            **({"note": "disconnected archive contents are recoverable for ~30 days"}
               if not enabled else {})}
