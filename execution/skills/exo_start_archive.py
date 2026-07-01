"""Force-start the Managed Folder Assistant so retention/archive rules run NOW (D-113; SOP: exchange-online).

The Managed Folder Assistant (MFA) normally processes a mailbox on its own ~7-day cycle, so a
freshly-applied retention policy or newly-enabled online archive only takes effect later. This
tells Exchange to process the mailbox immediately — the same thing the AdminToolKit does manually:
look up the PRIMARY mailbox GUID, then Start-ManagedFolderAssistant against it.

The primary GUID is `Get-Mailbox`'s `ExchangeGuid` (already read in the preflight) — exactly the
GUID `Get-MailboxLocation` returns for the Primary location. Using it (not the email identity)
avoids any ambiguity once an archive mailbox also exists. Nothing is created or deleted by THIS
call; it only triggers the scheduled assistant to run sooner.
"""
from __future__ import annotations

from typing import Any

NAME = "exo_start_archive"
DESCRIPTION = ("Force-start the Managed Folder Assistant on a mailbox so its retention policy and "
               "online-archive rules are applied NOW instead of waiting for the normal ~7-day "
               "cycle. Resolves the mailbox's PRIMARY GUID first, then runs "
               "Start-ManagedFolderAssistant against it. Use right after applying a retention "
               "policy (exo_set_retention_policy) or enabling the archive (exo_set_archive) when "
               "the user wants processing kicked off immediately. Pass `identities` (a list) to "
               "act on MANY mailboxes in ONE call — do NOT call this tool once per mailbox.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
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

_NO_GUID = "00000000-0000-0000-0000-000000000000"


def run(ctx, identity: str = "", identities: Any = None, **_: Any):
    exo = ctx.client("exo")
    wanted = [str(x).strip() for x in (identities or []) if str(x).strip()]
    if wanted:                                          # batch (D-110) — one call, many mailboxes
        results = ctx.map_progress(wanted[:500], lambda x: _one(exo, x))
        return {"ok": any(r.get("ok") for r in results), "started": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(exo, identity)


def _one(exo, identity: str) -> dict:
    from . import _exo_common as c
    # Preflight Get-Mailbox: confirms it exists / resolves to exactly one (same guardrail as every
    # EXO write) AND hands us the Primary mailbox GUID (ExchangeGuid) in the same read.
    mb, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return {**bad, "identity": identity}
    primary = mb.get("PrimarySmtpAddress") or identity
    guid = str(mb.get("ExchangeGuid") or "").strip()
    if not guid or guid == _NO_GUID:
        return {"ok": False, "identity": identity, "mailbox": primary, "step": "locate",
                "error": f"could not resolve the Primary mailbox GUID for '{primary}' "
                         f"(Get-Mailbox returned no ExchangeGuid)"}

    r = exo.invoke("Start-ManagedFolderAssistant", {"Identity": guid})
    if c.err(r):
        return {"ok": False, "identity": identity, "mailbox": primary, "primary_guid": guid,
                "step": "start", "error": c.err(r)}
    return {"ok": True, "identity": identity, "mailbox": primary, "primary_guid": guid,
            "managed_folder_assistant": "started",
            "note": "the assistant is now processing this mailbox in the background; retention "
                    "and archive tags apply over the next minutes-to-hours, not instantly"}
