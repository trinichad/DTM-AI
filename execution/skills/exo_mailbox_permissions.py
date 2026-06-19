"""WHO can access a mailbox — Full Access / Send As / Send on Behalf
(D-58; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_mailbox_permissions"
DESCRIPTION = ("Show WHO has access to a mailbox: Full Access users, Send As users, and Send "
               "on Behalf delegates. Pass a mailbox address for one, or leave it empty to "
               "report on ALL SHARED mailboxes (capped by limit). System entries are "
               "filtered out.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string",
                     "description": "one mailbox's address (optional — empty = every shared "
                                    "mailbox)"},
        "limit": {"type": "integer",
                  "description": "max shared mailboxes for the sweep (default 50, max 200)"},
    },
    "additionalProperties": False,
}

_SYSTEM = ("nt authority", "s-1-5-", "nampr", "eurpr")   # well-known/system principals


def _real(who: Any) -> bool:
    w = str(who or "").lower()
    return bool(w) and w not in ("default", "anonymous") \
        and not any(w.startswith(p) or p in w for p in _SYSTEM)


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def summarize(exo, mb: dict) -> dict[str, Any]:
    from . import _exo_common as c
    addr = str(mb.get("PrimarySmtpAddress") or "")
    out: dict[str, Any] = {"mailbox": addr, "display_name": mb.get("DisplayName"),
                           "type": mb.get("RecipientTypeDetails")}
    fa = exo.invoke("Get-MailboxPermission", {"Identity": addr})
    out["full_access"] = sorted({str(r.get("User")) for r in _rows(fa)
                                 if _real(r.get("User"))
                                 and "fullaccess" in " ".join(
                                     str(x) for x in (r.get("AccessRights") or [])).lower()}) \
        if not c.err(fa) else f"error: {c.err(fa)[:120]}"
    sa = exo.invoke("Get-RecipientPermission", {"Identity": addr})
    out["send_as"] = sorted({str(r.get("Trustee")) for r in _rows(sa)
                             if _real(r.get("Trustee"))}) \
        if not c.err(sa) else f"error: {c.err(sa)[:120]}"
    out["send_on_behalf"] = [str(x) for x in (mb.get("GrantSendOnBehalfTo") or [])]
    return out


def run(ctx, identity: str = "", limit: int = 50, **_: Any):
    from . import _exo_common as c
    exo = ctx.client("exo")
    if (identity or "").strip():
        mb, bad = c.get_one_mailbox(exo, identity.strip())
        if bad:
            return bad
        return {"ok": True, **summarize(exo, mb)}

    limit = max(1, min(int(limit or 50), 200))
    r = exo.invoke("Get-Mailbox", {"RecipientTypeDetails": "SharedMailbox",
                                   "ResultSize": limit})
    if c.err(r):
        e = c.err(r)
        if c.is_not_found(e):
            return {"ok": True, "count": 0, "mailboxes": [], "note": "no shared mailboxes"}
        return {"ok": False, "error": e}
    boxes = _rows(r)
    report = [summarize(exo, mb) for mb in boxes]
    out: dict[str, Any] = {"ok": True, "count": len(report), "mailboxes": report}
    if len(boxes) >= limit:
        out["note"] = f"showing the first {limit} shared mailboxes — raise `limit` for more"
    return out
