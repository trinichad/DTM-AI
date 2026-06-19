"""Create the transport rule that blocks auto-forwarding to external domains
(D-58; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_block_auto_forwarding"
DESCRIPTION = ("STOP AUTO-FORWARDING to external domains for the whole client: creates the "
               "standard transport rule that rejects auto-forwarded mail leaving the "
               "organization (users get the rejection text back). Already exists → clean "
               "no-op. Pair with exo_add_forwarding_alert to also get notified when someone "
               "sets up forwarding.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_DEFAULT_NAME = "Prevent auto forwarding of email to external domains"
_DEFAULT_TEXT = "Auto-forwarding has been disabled. Please contact your administrator."
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": f"rule name (default: '{_DEFAULT_NAME}')"},
        "reject_text": {"type": "string",
                        "description": "the message senders get back (default: "
                                       f"'{_DEFAULT_TEXT}')"},
    },
    "additionalProperties": False,
}


def run(ctx, name: str = "", reject_text: str = "", **_: Any):
    from . import _exo_common as c
    name = (name or "").strip() or _DEFAULT_NAME
    text = (reject_text or "").strip() or _DEFAULT_TEXT
    exo = ctx.client("exo")

    cur = exo.invoke("Get-TransportRule", {"Identity": name})
    rows = [x for x in (cur if isinstance(cur, list) else [cur]) if isinstance(x, dict)]
    if not c.err(cur) and rows:
        return {"ok": True, "rule": name,
                "note": "the auto-forward block rule already exists — nothing to do"}

    r = exo.invoke("New-TransportRule", {
        "Name": name, "FromScope": "InOrganization", "SentToScope": "NotInOrganization",
        "MessageTypeMatches": "AutoForward", "RejectMessageReasonText": text,
        "Confirm": False})
    if c.err(r):
        return {"ok": False, "step": "create", "error": c.err(r)}

    check = exo.invoke("Get-TransportRule", {"Identity": name})
    rows2 = [x for x in (check if isinstance(check, list) else [check]) if isinstance(x, dict)]
    if c.err(check) or not rows2:
        return {"ok": False, "step": "verify",
                "error": "New-TransportRule returned no error but the rule could not be "
                         "read back — check the Exchange admin center"}
    return {"ok": True, "rule_created": name, "reject_text": text,
            "note": "auto-forwarded mail to external domains is now rejected org-wide "
                    "(can take ~30 min to apply everywhere)"}
