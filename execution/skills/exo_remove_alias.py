"""Remove an email alias (proxy address) from a mailbox (D-65; SOP: exchange-online).
The opposite of exo_add_alias."""
from __future__ import annotations

from typing import Any

NAME = "exo_remove_alias"
DESCRIPTION = ("Remove an email ALIAS (extra address) from a mailbox. The PRIMARY address can't "
               "be removed this way (change it with exo_set_primary_smtp first). Remove MANY "
               "aliases from one mailbox in ONE call by passing `aliases` (a list) instead of "
               "`alias` — do NOT call this tool once per alias. Verifies the alias is gone before "
               "reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "the mailbox's primary email address"},
        "alias": {"type": "string", "description": "the alias address to remove"},
        "aliases": {"type": "array", "items": {"type": "string"},
                    "description": "remove MANY aliases from this mailbox in ONE call — a list of "
                                   "alias addresses; results come back together. Use this instead "
                                   "of calling the tool once per alias."},
    },
    "required": ["identity"],
    "additionalProperties": False,
}


def _aliases(mb: dict) -> list[str]:
    return [str(a) for a in (mb.get("EmailAddresses") or []) if isinstance(a, str)]


def run(ctx, identity: str, alias: str = "", aliases: Any = None, **_: Any):
    exo = ctx.client("exo")
    wanted = [a for a in (str(x).strip() for x in (aliases or [])) if a]
    if wanted:                                         # batch remove (D-110) — ONE call, ONE approval
        results = ctx.map_progress(wanted[:500], lambda a: _one(ctx, exo, identity, a))
        return {"ok": any(r.get("ok") for r in results), "mailbox": (identity or "").strip(),
                "aliases_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, exo, identity, alias)


def _one(ctx, exo, identity: str, alias: str) -> dict:
    from ..clients.exo import hashtable
    from . import _exo_common as c
    alias = (alias or "").strip().lower()
    if "@" not in alias or " " in alias:
        return {"ok": False, "alias": alias, "error": f"'{alias}' is not a valid email address"}
    mb, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return {**bad, "alias": alias}
    if str(mb.get("PrimarySmtpAddress") or "").lower() == alias:
        return {"ok": False, "alias": alias,
                "error": "that's the PRIMARY address — change it with "
                         "exo_set_primary_smtp instead of removing it"}
    if not any(a.lower() == f"smtp:{alias}" for a in _aliases(mb)):
        return {"ok": True, "mailbox": mb.get("PrimarySmtpAddress"), "alias": alias,
                "note": "that alias isn't on the mailbox — nothing to remove"}
    guard = c.needs_cloud_management(mb, {"EmailAddresses": True}, label="remove the alias")
    if guard:
        return {**guard, "alias": alias}

    r = exo.invoke("Set-Mailbox", {"Identity": identity, "Confirm": False,
                                   "EmailAddresses": hashtable({"Remove": f"smtp:{alias}"})})
    if c.err(r):
        return {"ok": False, "alias": alias, "step": "remove alias", "error": c.err(r)}
    after, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return {"ok": False, "alias": alias, "step": "verify",
                "error": f"re-read failed — {bad.get('error')}"}
    if any(a.lower() == f"smtp:{alias}" for a in _aliases(after)):
        return {"ok": False, "alias": alias, "step": "verify",
                "error": f"Set-Mailbox returned no error but '{alias}' is still on the "
                         f"mailbox — check Exchange directly"}
    return {"ok": True, "mailbox": after.get("PrimarySmtpAddress"), "alias_removed": alias}
