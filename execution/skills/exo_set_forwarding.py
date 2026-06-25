"""Configure (or clear) SMTP forwarding on a mailbox (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_set_forwarding"
DESCRIPTION = ("Set up email FORWARDING on a mailbox: forward to another address, choosing "
               "whether a copy is also kept in the original mailbox (keep_copy). Pass an empty "
               "forward_to to TURN FORWARDING OFF. Verifies the change before reporting it. Pass "
               "`identities` (a list) to act on MANY mailboxes in ONE call — do NOT call this "
               "tool once per mailbox.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"            # mail redirection is a classic exfiltration vector — always reviewed
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
        "forward_to": {"type": "string",
                       "description": "address to forward to — EMPTY STRING disables forwarding"},
        "keep_copy": {"type": "boolean",
                      "description": "true = deliver to the original mailbox AND forward "
                                     "(default); false = forward only, nothing kept"},
    },
    "required": ["forward_to"],
    "additionalProperties": False,
}


def run(ctx, identity: str = "", forward_to: str = "", keep_copy: bool = True,
        identities: Any = None, **_: Any):
    exo = ctx.client("exo")
    wanted = [str(x).strip() for x in (identities or []) if str(x).strip()]
    if wanted:                                          # batch (D-110) — one call, many mailboxes
        results = ctx.map_progress(wanted[:500], lambda x: _one(exo, x, forward_to, keep_copy))
        return {"ok": any(r.get("ok") for r in results), "forwarding_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(exo, identity, forward_to, keep_copy)


def _one(exo, identity: str, forward_to: str, keep_copy: bool = True) -> dict:
    from . import _exo_common as c
    forward_to = (forward_to or "").strip().lower()
    if forward_to:                                    # enable forwarding
        if "@" not in forward_to or " " in forward_to:
            return {"ok": False, "identity": identity,
                    "error": f"'{forward_to}' is not a valid email address"}
        r = c.set_and_verify(exo, identity,
                             {"ForwardingSmtpAddress": forward_to,
                              "DeliverToMailboxAndForward": bool(keep_copy)},
                             {"ForwardingSmtpAddress": f"smtp:{forward_to}",
                              "DeliverToMailboxAndForward": bool(keep_copy)},
                             label="set forwarding")
        r.setdefault("identity", identity)
        if r.get("ok"):
            r["note"] = (f"forwarding to {forward_to}; a copy "
                         + ("is kept in" if keep_copy else "is NOT kept in")
                         + " the original mailbox")
        return r
    r = c.set_and_verify(exo, identity,
                         {"ForwardingSmtpAddress": None,
                          "DeliverToMailboxAndForward": False},
                         {"ForwardingSmtpAddress": None},
                         label="clear forwarding")
    r.setdefault("identity", identity)
    if r.get("ok"):
        r["note"] = "forwarding disabled"
    return r
