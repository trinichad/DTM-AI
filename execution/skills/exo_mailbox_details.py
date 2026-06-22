"""Full admin view of one mailbox — config + sizes (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_mailbox_details"
DESCRIPTION = ("Show ONE mailbox's full admin details: type, aliases, hidden-from-address-book, "
               "forwarding, max send/receive sizes, retention policy, online-archive state, "
               "whether the user is AD-synced and whether Exchange CLOUD MANAGEMENT is enabled, "
               "and the CURRENT SIZE of the mailbox and its archive. Use this to check a mailbox's "
               "configuration (including whether cloud management is already set) or to verify a "
               "change.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "the mailbox's primary email address"},
    },
    "required": ["identity"],
    "additionalProperties": False,
}

_NO_ARCHIVE_GUID = "00000000-0000-0000-0000-000000000000"


def _stats(exo, identity: str, archive: bool) -> dict[str, Any]:
    from . import _exo_common as c
    params: dict[str, Any] = {"Identity": identity}
    if archive:
        params["Archive"] = True
    r = exo.invoke("Get-MailboxStatistics", params)
    if c.err(r):
        return {"error": c.err(r)}
    row = r[0] if isinstance(r, list) and r else r
    if not isinstance(row, dict):
        return {"error": "no statistics returned"}
    return {"size": row.get("TotalItemSize"), "items": row.get("ItemCount"),
            "deleted_size": row.get("TotalDeletedItemSize")}


def run(ctx, identity: str, **_: Any):
    from . import _exo_common as c
    exo = ctx.client("exo")
    mb, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return bad
    primary = str(mb.get("PrimarySmtpAddress") or identity)
    archive_guid = str(mb.get("ArchiveGuid") or "")
    has_archive = (bool(archive_guid) and archive_guid != _NO_ARCHIVE_GUID
                   and str(mb.get("ArchiveState")) != "None")

    fwd = mb.get("ForwardingSmtpAddress")
    out: dict[str, Any] = {
        "ok": True,
        "mailbox": primary,
        "display_name": mb.get("DisplayName"),
        "type": mb.get("RecipientTypeDetails"),
        "sign_in_id": mb.get("MicrosoftOnlineServicesID") or mb.get("UserPrincipalName"),
        "dir_synced": bool(mb.get("IsDirSynced")),              # identity mastered on-prem AD?
        "cloud_managed": bool(mb.get("IsExchangeCloudManaged")),  # EXO masters mailbox settings? (D-91)
        "addresses": [a for a in (mb.get("EmailAddresses") or []) if isinstance(a, str)],
        "hidden_from_address_book": bool(mb.get("HiddenFromAddressListsEnabled")),
        "forwarding": ({"to": str(fwd).removeprefix("smtp:"),
                        "keeps_copy": bool(mb.get("DeliverToMailboxAndForward"))}
                       if fwd else "off"),
        "max_send_size": mb.get("MaxSendSize"),
        "max_receive_size": mb.get("MaxReceiveSize"),
        "retention_policy": mb.get("RetentionPolicy"),
        "archive": "enabled" if has_archive else "disabled",
        "usage": _stats(exo, primary, archive=False),
    }
    if has_archive:
        out["archive_usage"] = _stats(exo, primary, archive=True)
    return out
