"""Enable AUTO-EXPANDING archiving on a mailbox (D-55; SOP: exchange-online).

ENABLE-ONLY by Microsoft's design: once auto-expanding archiving is on for a mailbox it
CANNOT be turned off — so there is deliberately no 'disable' side to this tool.
"""
from __future__ import annotations

from typing import Any

NAME = "exo_enable_autoexpanding_archive"
DESCRIPTION = ("Enable AUTO-EXPANDING archiving on a mailbox — the online archive grows "
               "automatically beyond 100 GB (up to ~1.5 TB) as it fills. The regular online "
               "archive must already be enabled (exo_set_archive) and the license must include "
               "it (Exchange Online Plan 2 / E3+). WARNING: this CANNOT be undone for the "
               "mailbox once enabled — confirm with the user before running. Verifies before "
               "reporting success. Pass `identities` (a list) to act on MANY mailboxes in ONE "
               "call — do NOT call this tool once per mailbox.")
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
    },
    "required": [],
    "additionalProperties": False,
}

_NO_ARCHIVE_GUID = "00000000-0000-0000-0000-000000000000"


def run(ctx, identity: str = "", identities: Any = None, **_: Any):
    exo = ctx.client("exo")
    wanted = [str(x).strip() for x in (identities or []) if str(x).strip()]
    if wanted:                                          # batch (D-110) — one call, many mailboxes
        results = ctx.map_progress(wanted[:500], lambda x: _one(exo, x))
        return {"ok": any(r.get("ok") for r in results),
                "auto_expanding_archive_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(exo, identity)


def _one(exo, identity: str) -> dict:
    from . import _exo_common as c
    mb, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return {**bad, "identity": identity}
    primary = mb.get("PrimarySmtpAddress") or identity
    if bool(mb.get("AutoExpandingArchiveEnabled")):
        return {"ok": True, "identity": identity, "mailbox": primary,
                "note": "auto-expanding archiving is already enabled — nothing to do"}
    guid = str(mb.get("ArchiveGuid") or "")
    if not guid or guid == _NO_ARCHIVE_GUID or str(mb.get("ArchiveState")) == "None":
        return {"ok": False, "identity": identity,
                "error": f"'{primary}' has no online archive yet — enable it "
                         f"first with exo_set_archive, then auto-expanding"}

    r = exo.invoke("Enable-Mailbox", {"Identity": identity, "AutoExpandingArchive": True,
                                      "Confirm": False})
    if c.err(r):
        e = c.err(r)
        hint = (" (the license may not include it — needs Exchange Online Plan 2 / E3+)"
                if "license" in e.lower() or "plan" in e.lower() else "")
        return {"ok": False, "identity": identity, "step": "enable", "error": e + hint}

    after, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return {"ok": False, "identity": identity, "step": "verify",
                "error": f"re-read failed — {bad.get('error')}"}
    if not bool(after.get("AutoExpandingArchiveEnabled")):
        return {"ok": False, "identity": identity, "step": "verify",
                "error": "Enable-Mailbox returned no error but AutoExpandingArchiveEnabled is "
                         "still off — check Exchange directly"}
    return {"ok": True, "identity": identity, "mailbox": primary,
            "auto_expanding_archive": "enabled",
            "note": "permanent for this mailbox (Microsoft does not allow turning it off); "
                    "extra archive space provisions gradually as the archive fills, not "
                    "instantly — up to ~1.5 TB total"}
