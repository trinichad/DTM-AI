"""Delete an Exchange Online mailbox — DESTRUCTIVE, hand-written (D-54; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_delete_mailbox"
DESCRIPTION = ("DELETE a mailbox (shared or user) in the client's Exchange Online. Soft delete — "
               "recoverable in Exchange for ~30 days. WARNING: deleting a USER mailbox also "
               "deletes that user account (Exchange behavior); a shared mailbox is just removed. "
               "Pass the exact address. Every run requires fresh owner approval (cannot be "
               "disabled). Verify with exo_list_mailboxes after.")
SOURCE = "m365"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True     # floor — dispatch forces approval for destructive regardless
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string",
                     "description": "the mailbox's primary email address (exact)"},
    },
    "required": ["identity"],
    "additionalProperties": False,
}


def _err(r: Any) -> str:
    return str(r.get("error")) if isinstance(r, dict) and r.get("error") else ""


def run(ctx, identity: str, **_: Any):
    identity = (identity or "").strip()
    if "@" not in identity or " " in identity:
        return {"ok": False, "error": f"'{identity}' is not a valid mailbox address"}
    exo = ctx.client("exo")

    # Pre-flight: the mailbox must exist and resolve to EXACTLY one object; report its type so
    # the audit trail shows precisely what was deleted.
    found = exo.invoke("Get-Mailbox", {"Identity": identity})
    if _err(found):
        e = _err(found)
        if "NotFound" in e or "couldn't be found" in e:
            return {"ok": False, "error": f"no mailbox '{identity}' — nothing to delete"}
        return {"ok": False, "step": "preflight", "error": e}
    rows = found if isinstance(found, list) else [found]
    rows = [r for r in rows if isinstance(r, dict)]
    if len(rows) != 1:
        return {"ok": False, "error": f"'{identity}' matched {len(rows)} mailboxes — must resolve "
                                      f"to exactly one (use the primary SMTP address)"}
    mb_type = str(rows[0].get("RecipientTypeDetails") or "Unknown")

    r = exo.invoke_destructive("Remove-Mailbox", {"Identity": identity, "Confirm": False})
    if _err(r):
        return {"ok": False, "step": "delete", "mailbox_type": mb_type, "error": _err(r)}

    # Verify it's really gone — never report a deletion that didn't happen.
    check = exo.invoke("Get-Mailbox", {"Identity": identity})
    e = _err(check)
    still_there = not (e and ("NotFound" in e or "couldn't be found" in e))
    if still_there and not e:
        rows2 = check if isinstance(check, list) else [check]
        still_there = any(isinstance(x, dict) and x for x in rows2)
    if still_there:
        return {"ok": False, "step": "verify", "mailbox_type": mb_type,
                "error": f"Remove-Mailbox returned no error but '{identity}' is still visible — "
                         f"check Exchange directly before retrying"}
    return {"ok": True, "deleted": identity, "mailbox_type": mb_type,
            "mode": "soft_delete (recoverable in Exchange ~30 days)",
            "note": ("the associated user account was deleted too" if "User" in mb_type
                     else "shared mailbox removed")}
