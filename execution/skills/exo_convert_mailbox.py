"""Convert a mailbox between regular (user) and shared (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_convert_mailbox"
DESCRIPTION = ("Convert a mailbox: a regular USER mailbox to a SHARED mailbox, or a shared "
               "mailbox back to a regular one. Converting to shared frees the license (remove "
               "it afterwards); converting to regular REQUIRES assigning a license within 30 "
               "days or the mailbox is disabled. Verifies the conversion before reporting it. "
               "Pass `identities` (a list) to act on MANY mailboxes in ONE call — do NOT call "
               "this tool once per mailbox.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_TYPES = {"shared": ("Shared", "SharedMailbox"), "regular": ("Regular", "UserMailbox")}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "the mailbox's primary email address"},
        "identities": {"type": "array", "items": {"type": "string"},
                       "description": "act on MANY in ONE call — a list of mailbox addresses; "
                                      "results come back together. Use this instead of calling "
                                      "the tool once per mailbox."},
        "to": {"type": "string", "enum": ["shared", "regular"],
               "description": "target type: 'shared' or 'regular'"},
    },
    "required": ["to"],
    "additionalProperties": False,
}


def run(ctx, identity: str = "", to: str = "", identities: Any = None, **_: Any):
    exo = ctx.client("exo")
    wanted = [str(x).strip() for x in (identities or []) if str(x).strip()]
    if wanted:                                          # batch (D-110) — one call, many mailboxes
        results = [_one(exo, x, to) for x in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "convert_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(exo, identity, to)


def _one(exo, identity: str, to: str) -> dict:
    from . import _exo_common as c
    kind = _TYPES.get((to or "").strip().lower())
    if not kind:
        return {"ok": False, "identity": identity, "error": "`to` must be 'shared' or 'regular'"}
    set_type, want_details = kind
    mb, bad = c.get_one_mailbox(exo, identity)
    if bad:
        return {**bad, "identity": identity}
    addr = mb.get("PrimarySmtpAddress") or identity
    if str(mb.get("RecipientTypeDetails")) == want_details:
        return {"ok": True, "identity": identity, "mailbox": addr,
                "note": f"already a {to} mailbox — nothing to do"}

    r = exo.invoke("Set-Mailbox", {"Identity": identity, "Type": set_type, "Confirm": False})
    if c.err(r):
        return {"ok": False, "identity": identity, "step": "convert", "error": c.err(r)}

    # Conversion is eventually-consistent — poll the re-read instead of failing on a stale one (D-99).
    flipped, after = c.settle(lambda: c.get_one_mailbox(exo, identity)[0] or mb,
                              lambda m: str(m.get("RecipientTypeDetails")) == want_details)
    if flipped:
        note = ("now SHARED — remove the user's license to free it (shared mailboxes under 50 GB "
                "need none)" if set_type == "Shared"
                else "now a REGULAR mailbox — assign a license within 30 days or Exchange will "
                     "disable it (use m365_assign_license)")
        return {"ok": True, "identity": identity, "mailbox": addr, "to": to,
                "recipient_type": after.get("RecipientTypeDetails"), "note": note}

    # Set-Mailbox returned no error but the type hasn't flipped within the poll window. This is
    # almost always propagation lag, not a failure — say so, and don't imply the convert was rejected.
    return {"ok": False, "identity": identity, "step": "verify", "pending": True, "mailbox": addr,
            "recipient_type": after.get("RecipientTypeDetails"),
            "error": (f"Exchange accepted the conversion of {addr} to {to}, but it still shows "
                      f"'{after.get('RecipientTypeDetails')}' after a short poll. Mailbox-type "
                      f"conversions can take a few minutes to propagate — re-check with "
                      f"exo_mailbox_details shortly; do NOT re-run the convert (it likely took).")}
