"""Add an email alias (proxy address) to a mailbox (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_add_alias"
DESCRIPTION = ("Add an email ALIAS (additional address) to a mailbox — mail sent to the alias "
               "lands in the same mailbox; the primary address is unchanged. Pass the mailbox's "
               "primary address and the new alias. Add MANY aliases to one mailbox in ONE call by "
               "passing `aliases` (a list) instead of `alias` — do NOT call this tool once per "
               "alias. Verifies the alias before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "the mailbox's primary email address"},
        "alias": {"type": "string", "description": "the alias address to add, "
                                                   "e.g. sales@demodomain.com"},
        "aliases": {"type": "array", "items": {"type": "string"},
                    "description": "add MANY aliases to this mailbox in ONE call — a list of "
                                   "alias addresses; results come back together. Use this instead "
                                   "of calling the tool once per alias."},
    },
    "required": ["identity"],
    "additionalProperties": False,
}


def _aliases(mb: dict) -> list[str]:
    addrs = mb.get("EmailAddresses") or []
    return [str(a) for a in addrs if isinstance(a, str)]


def run(ctx, identity: str, alias: str = "", aliases: Any = None, **_: Any):
    exo = ctx.client("exo")
    wanted = [a for a in (str(x).strip() for x in (aliases or [])) if a]
    if wanted:                                         # batch add (D-110) — ONE call, ONE approval
        results = [_one(ctx, exo, identity, a) for a in wanted[:500]]
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
    if any(a.lower() == f"smtp:{alias}" for a in _aliases(mb)):
        return {"ok": True, "mailbox": mb.get("PrimarySmtpAddress"), "alias": alias,
                "note": "that alias is already on the mailbox — nothing to do"}
    guard = c.needs_cloud_management(mb, {"EmailAddresses": True}, label="add the alias")
    if guard:
        return {**guard, "alias": alias}

    r = exo.invoke("Set-Mailbox", {"Identity": identity, "Confirm": False,
                                   "EmailAddresses": hashtable({"Add": f"smtp:{alias}"})})
    if c.err(r):
        return {"ok": False, "alias": alias, "step": "add alias", "error": c.err(r)}

    after, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return {"ok": False, "alias": alias, "step": "verify",
                "error": f"re-read failed — {bad.get('error')}"}
    if not any(a.lower() == f"smtp:{alias}" for a in _aliases(after)):
        return {"ok": False, "alias": alias, "step": "verify",
                "error": f"Set-Mailbox returned no error but '{alias}' is not on the mailbox — "
                         f"check Exchange directly"}
    return {"ok": True, "mailbox": after.get("PrimarySmtpAddress"), "alias_added": alias,
            "all_addresses": _aliases(after)}
