"""Alert the admin when anyone sets up mail forwarding/redirect (D-58; SOP: exchange-online).

Uses the Security & Compliance endpoint (New-ProtectionAlert) via invoke_compliance() —
same Exchange sign-in, different token audience (see the D-58 SOP amendment).
"""
from __future__ import annotations

from typing import Any

NAME = "exo_add_forwarding_alert"
DESCRIPTION = ("Create the FORWARDING ALERT: whenever anyone in the client sets up "
               "auto-forwarding, a redirect rule, or a forwarding mail-flow rule, an email "
               "alert is sent to notify_email. ALWAYS ask the user which email address "
               "should receive the alerts — never guess one. Already exists → clean no-op. "
               "Pairs with exo_block_auto_forwarding.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_DEFAULT_NAME = "Forwarding/redirect rule was created"
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "notify_email": {"type": "string",
                         "description": "the address that receives the alerts — ASK the user "
                                        "which one, never assume"},
        "name": {"type": "string", "description": f"alert name (default '{_DEFAULT_NAME}')"},
    },
    "required": ["notify_email"],
    "additionalProperties": False,
}


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def run(ctx, notify_email: str, name: str = "", **_: Any):
    from . import _exo_common as c
    notify_email = (notify_email or "").strip()
    if "@" not in notify_email or " " in notify_email:
        return {"ok": False, "error": f"'{notify_email}' is not a valid email address"}
    name = (name or "").strip() or _DEFAULT_NAME
    exo = ctx.client("exo")

    cur = exo.invoke_compliance("Get-ProtectionAlert", {"Identity": name})
    if not c.err(cur) and _rows(cur):
        return {"ok": True, "alert": name,
                "note": "a protection alert with this name already exists — nothing to do"}

    r = exo.invoke_compliance("New-ProtectionAlert", {
        "Name": name, "Category": "ThreatManagement", "ThreatType": "Activity",
        "Operation": ["MailRedirect"], "Severity": "Low",   # D-66: enum is Low|Medium|High,
        "NotifyUser": [notify_email], "AggregationType": "None",   # "Informational" is invalid
        "Description": ("Triggered when someone in the organization sets up "
                        "auto-forwarding, email forwarding, a redirect rule, or a "
                        "forwarding mail-flow rule.")})
    if c.err(r):
        e = c.err(r)
        hint = (" (the signing admin may need a Compliance role — Security Administrator "
                "or Organization Management)" if "401" in e or "403" in e else "")
        return {"ok": False, "step": "create", "error": e + hint}

    check = exo.invoke_compliance("Get-ProtectionAlert", {"Identity": name})
    if c.err(check) or not _rows(check):
        return {"ok": False, "step": "verify",
                "error": "New-ProtectionAlert returned no error but the alert could not be "
                         "read back — check the Defender/Purview portal"}
    return {"ok": True, "alert_created": name, "notifies": notify_email,
            "note": "alerts arrive by email and appear in the Defender portal's alert list"}
