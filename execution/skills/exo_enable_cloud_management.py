"""Enable Exchange cloud management for an AD-synced mailbox (D-91; SOP: exchange-online).

For a user synced from on-prem Active Directory (IsDirSynced=True), mailbox settings (address-book
visibility, aliases, primary SMTP) are mastered on-prem and can't be edited in Exchange Online
until the mailbox is flagged cloud-managed. `Set-Mailbox -IsExchangeCloudManaged $true` flips that,
so the cloud mailbox tools (exo_set_gal_visibility, exo_add_alias, exo_set_primary_smtp) can take
effect. AD still owns identity, password, enabled/disabled status, and group membership.
Follows the D-43 rule: preflight Get-Mailbox → Set-Mailbox → re-read → compare (never report an
unverified write).
"""
from __future__ import annotations

from typing import Any

NAME = "exo_enable_cloud_management"
DESCRIPTION = ("Let Exchange Online manage a synced user's MAILBOX settings (address-book "
               "visibility, aliases, primary email) for an AD-synced Microsoft 365 user. On-prem "
               "Active Directory still controls their sign-in, password, and groups — this only "
               "moves mailbox settings to the cloud so the mailbox tools can edit them. Verifies "
               "the change before reporting success. Pass `identities` (a list) to act on MANY "
               "mailboxes in ONE call — do NOT call this tool once per mailbox.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"           # foundational mailbox-authority change, but reversible + no data loss
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string",
                     "description": "the user's mailbox / primary email address"},
        "identities": {"type": "array", "items": {"type": "string"},
                       "description": "act on MANY in ONE call — a list of mailbox addresses; "
                                      "results come back together. Use this instead of calling "
                                      "the tool once per mailbox."},
    },
    "required": [],
    "additionalProperties": False,
}


def describe_approval(ctx, args: dict):
    """Plain-language approval-card preview (D-90)."""
    return {"Enable cloud management for": str(args.get("identity") or ""),
            "Effect": "Exchange Online will manage mailbox settings (address book, aliases, "
                      "primary email); on-prem AD still owns sign-in, password, and groups"}


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def run(ctx, identity: str = "", identities: Any = None, **_: Any):
    exo = ctx.client("exo")
    wanted = [str(x).strip() for x in (identities or []) if str(x).strip()]
    if wanted:                                          # batch (D-110) — one call, many mailboxes
        results = ctx.map_progress(wanted[:500], lambda x: _one(exo, x))
        return {"ok": any(r.get("ok") for r in results), "cloud_management_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(exo, identity)


def _one(exo, identity: str) -> dict:
    from . import _exo_common as c
    mb, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return {**bad, "identity": identity}
    dirsynced = _truthy(mb.get("IsDirSynced"))
    if _truthy(mb.get("IsExchangeCloudManaged")):
        return {"ok": True, "identity": identity,
                "mailbox": mb.get("PrimarySmtpAddress") or identity,
                "IsDirSynced": mb.get("IsDirSynced"), "IsExchangeCloudManaged": True,
                "note": "already cloud-managed — nothing to do; the cloud mailbox tools "
                        "(GAL visibility, aliases, primary SMTP) already apply"}
    r = c.set_and_verify(exo, identity, {"IsExchangeCloudManaged": True},
                         {"IsExchangeCloudManaged": True},
                         label="enable Exchange cloud management")
    r.setdefault("identity", identity)
    if r.get("ok"):
        r["IsDirSynced"] = mb.get("IsDirSynced")
        r["note"] = ("Exchange Online now manages this mailbox's settings (address-book "
                     "visibility, aliases, primary SMTP). On-prem AD still owns identity, "
                     "password, enabled/disabled status, and group membership."
                     + ("" if dirsynced else
                        "  Note: this mailbox is NOT AD-synced (IsDirSynced=False) — it was "
                        "likely already cloud-managed; no harm done."))
    return r
