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
               "Verifies the state before reporting success. Pass `identities` (a list) to act "
               "on MANY mailboxes in ONE call — do NOT call this tool once per mailbox.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "the mailbox's primary email address"},
        "identities": {"type": "array", "items": {"type": "string"},
                       "description": "act on MANY in ONE call — a list of mailbox addresses; "
                                      "results come back together. Use this instead of calling "
                                      "the tool once per mailbox."},
        "enabled": {"type": "boolean", "description": "true = enable the archive, false = disable"},
    },
    "required": ["enabled"],
    "additionalProperties": False,
}

_NO_ARCHIVE_GUID = "00000000-0000-0000-0000-000000000000"


def _has_archive(mb: dict) -> bool:
    guid = str(mb.get("ArchiveGuid") or "")
    return bool(guid) and guid != _NO_ARCHIVE_GUID and str(mb.get("ArchiveState")) != "None"


def run(ctx, identity: str = "", enabled: bool = False, identities: Any = None, **_: Any):
    exo = ctx.client("exo")
    wanted = [str(x).strip() for x in (identities or []) if str(x).strip()]
    if wanted:                                          # batch (D-110) — one call, many mailboxes
        results = [_one(exo, x, enabled) for x in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "archive_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(exo, identity, enabled)


def _one(exo, identity: str, enabled: bool) -> dict:
    from . import _exo_common as c
    mb, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return {**bad, "identity": identity}
    if _has_archive(mb) == bool(enabled):
        return {"ok": True, "identity": identity, "mailbox": mb.get("PrimarySmtpAddress"),
                "note": f"archive is already {'enabled' if enabled else 'disabled'} — nothing to do"}

    cmdlet = "Enable-Mailbox" if enabled else "Disable-Mailbox"
    r = exo.invoke(cmdlet, {"Identity": identity, "Confirm": False})
    if c.err(r):
        e = c.err(r)
        hint = (" (the license may not include archiving — needs Exchange Online Plan 2 / E3+)"
                if enabled and ("license" in e.lower() or "plan" in e.lower()) else "")
        return {"ok": False, "identity": identity, "step": cmdlet, "error": e + hint}

    after, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return {"ok": False, "identity": identity, "step": "verify",
                "error": f"re-read failed — {bad.get('error')}"}
    if _has_archive(after) != bool(enabled):
        return {"ok": False, "identity": identity, "step": "verify",
                "error": f"{cmdlet} returned no error but the archive state didn't change — "
                         f"check Exchange directly (provisioning can lag a few minutes)"}
    return {"ok": True, "identity": identity, "mailbox": after.get("PrimarySmtpAddress"),
            "archive": "enabled" if enabled else "disabled",
            **({"note": "disconnected archive contents are recoverable for ~30 days"}
               if not enabled else {})}
