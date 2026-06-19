"""Check mailbox junk-email filter status (D-58; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_junk_filter_status"
DESCRIPTION = ("Check whether the JUNK EMAIL filter is enabled on a mailbox — one mailbox, or "
               "sweep every user mailbox (capped by `limit`) and report who has it on/off. "
               "Change it with exo_set_junk_filter.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "one mailbox (optional — empty sweeps "
                                                      "all user mailboxes)"},
        "limit": {"type": "integer",
                  "description": "max mailboxes for a sweep (default 100, max 500)"},
    },
    "additionalProperties": False,
}


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def run(ctx, identity: str = "", limit: int = 100, **_: Any):
    from . import _exo_common as c
    exo = ctx.client("exo")
    if (identity or "").strip():
        r = exo.invoke("Get-MailboxJunkEmailConfiguration", {"Identity": identity.strip()})
        if c.err(r):
            return {"ok": False, "error": c.err(r)}
        rows = _rows(r)
        if not rows:
            return {"ok": False, "error": f"no junk configuration for '{identity}'"}
        return {"ok": True, "mailbox": identity.strip(),
                "junk_filter": "enabled" if rows[0].get("Enabled") else "disabled"}

    limit = max(1, min(int(limit or 100), 500))
    r = exo.invoke("Get-Mailbox", {"RecipientTypeDetails": "UserMailbox",
                                   "ResultSize": limit})
    if c.err(r):
        return {"ok": False, "error": c.err(r)}
    boxes = _rows(r)
    enabled, disabled, errors = [], [], []
    for mb in boxes:
        addr = str(mb.get("PrimarySmtpAddress") or "")
        jc = exo.invoke("Get-MailboxJunkEmailConfiguration", {"Identity": addr})
        if c.err(jc):
            errors.append({"mailbox": addr, "error": c.err(jc)[:120]})
        elif _rows(jc) and _rows(jc)[0].get("Enabled"):
            enabled.append(addr)
        else:
            disabled.append(addr)
    out: dict[str, Any] = {"ok": True, "checked": len(boxes),
                           "junk_filter_enabled": enabled,
                           "junk_filter_disabled": disabled,
                           "summary": {"enabled": len(enabled), "disabled": len(disabled)}}
    if errors:
        out["errors"] = errors
    if len(boxes) >= limit:
        out["note"] = f"checked the first {limit} user mailboxes — raise `limit` for more"
    return out
