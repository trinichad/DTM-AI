"""Enable/disable the junk-email filter — one mailbox or every user mailbox
(D-58; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_set_junk_filter"
DESCRIPTION = ("Enable or DISABLE the JUNK EMAIL filter on a mailbox — on a specific LIST of "
               "mailboxes via `identities` (act on MANY in ONE call — do NOT call this tool once "
               "per mailbox), or on EVERY user mailbox at once (leave both empty). MSPs using an "
               "external spam filter (e.g. Proofpoint) typically disable it so mail isn't "
               "filtered twice. Single/list mailboxes are fully verified; whole-tenant mode "
               "reports applied/failed per mailbox and verifies a sample.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "description": "true = junk filter on, false = off"},
        "identity": {"type": "string",
                     "description": "one mailbox (optional — empty applies to ALL user "
                                    "mailboxes)"},
        "identities": {"type": "array", "items": {"type": "string"},
                       "description": "act on MANY in ONE call — a list of mailbox addresses; "
                                      "results come back together. Use this instead of calling "
                                      "the tool once per mailbox."},
        "limit": {"type": "integer",
                  "description": "whole-tenant mode: max mailboxes (default 500, max 1000)"},
    },
    "required": ["enabled"],
    "additionalProperties": False,
}


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def _status(c, exo, addr: str):
    jc = exo.invoke("Get-MailboxJunkEmailConfiguration", {"Identity": addr})
    if c.err(jc) or not _rows(jc):
        return None
    return bool(_rows(jc)[0].get("Enabled"))


def _one(exo, identity: str, enabled: bool) -> dict:    # one mailbox, fully verified
    from . import _exo_common as c
    want = bool(enabled)
    addr = (identity or "").strip()
    if not addr:
        return {"ok": False, "identity": identity, "error": "no mailbox identity given"}
    if _status(c, exo, addr) is want:
        return {"ok": True, "identity": addr, "mailbox": addr,
                "note": f"junk filter is already {'on' if want else 'off'} — nothing to do"}
    r = exo.invoke("Set-MailboxJunkEmailConfiguration",
                   {"Identity": addr, "Enabled": want, "Confirm": False})
    if c.err(r):
        return {"ok": False, "identity": addr, "step": "set", "error": c.err(r)}
    if _status(c, exo, addr) is not want:
        return {"ok": False, "identity": addr, "step": "verify",
                "error": "the change didn't stick — known Exchange quirk: mailboxes the "
                         "user has never signed in to can refuse junk configuration"}
    return {"ok": True, "identity": addr, "mailbox": addr,
            "junk_filter": "enabled" if want else "disabled"}


def run(ctx, enabled: bool, identity: str = "", identities: Any = None,
        limit: int = 500, **_: Any):
    from . import _exo_common as c
    exo = ctx.client("exo")
    want = bool(enabled)

    wanted = [str(x).strip() for x in (identities or []) if str(x).strip()]
    if wanted:                                          # batch (D-110) — one call, many mailboxes
        results = ctx.map_progress(wanted[:500], lambda x: _one(exo, x, enabled))
        return {"ok": any(r.get("ok") for r in results), "junk_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}

    if (identity or "").strip():                       # ── one mailbox, fully verified ──
        return _one(exo, identity, enabled)

    # ── bulk: every user mailbox ──
    limit = max(1, min(int(limit or 500), 1000))
    r = exo.invoke("Get-Mailbox", {"RecipientTypeDetails": "UserMailbox",
                                   "ResultSize": limit})
    if c.err(r):
        return {"ok": False, "error": c.err(r)}
    boxes = [str(mb.get("PrimarySmtpAddress") or "") for mb in _rows(r)]
    applied, failed = [], []
    for addr in boxes:
        s = exo.invoke("Set-MailboxJunkEmailConfiguration",
                       {"Identity": addr, "Enabled": want, "Confirm": False})
        (failed.append({"mailbox": addr, "error": c.err(s)[:120]}) if c.err(s)
         else applied.append(addr))
    sample = [a for a in applied[:5]]
    verified = sum(1 for a in sample if _status(c, exo, a) is want)
    out: dict[str, Any] = {
        "ok": not failed, "junk_filter": "enabled" if want else "disabled",
        "applied": len(applied), "failed": len(failed),
        "sample_verified": f"{verified}/{len(sample)}" if sample else "n/a"}
    if failed:
        out["failures"] = failed
        out["note"] = ("some mailboxes refused — known Exchange quirk for mailboxes the "
                       "user has never signed in to; re-run for them later")
    if len(boxes) >= limit:
        out["limit_note"] = f"processed the first {limit} user mailboxes — raise `limit` " \
                            f"(max 1000) for more"
    return out
