"""Remove the forwarding/redirect protection alert (D-65; SOP: exchange-online).
The opposite of exo_add_forwarding_alert. Uses the Security & Compliance endpoint."""
from __future__ import annotations

from typing import Any

NAME = "exo_remove_forwarding_alert"
DESCRIPTION = ("Remove the forwarding/redirect PROTECTION ALERT created by "
               "exo_add_forwarding_alert — admins stop being emailed when someone sets up "
               "forwarding. Verifies it's gone before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_DEFAULT_NAME = "Forwarding/redirect rule was created"
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": f"alert name (default '{_DEFAULT_NAME}')"},
    },
    "additionalProperties": False,
}


def _rows(r: Any) -> list[dict]:
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]


def run(ctx, name: str = "", **_: Any):
    from . import _exo_common as c
    name = (name or "").strip() or _DEFAULT_NAME
    exo = ctx.client("exo")
    cur = exo.invoke_compliance("Get-ProtectionAlert", {"Identity": name})
    if c.err(cur) or not _rows(cur):
        return {"ok": True, "alert": name, "note": "no such alert — nothing to remove"}
    r = exo.invoke_compliance("Remove-ProtectionAlert", {"Identity": name, "Confirm": False})
    if c.err(r):
        return {"ok": False, "step": "remove", "error": c.err(r)}
    check = exo.invoke_compliance("Get-ProtectionAlert", {"Identity": name})
    if not c.err(check) and _rows(check):
        return {"ok": False, "step": "verify",
                "error": f"the alert '{name}' still exists after removal — check the "
                         f"Defender/Purview portal"}
    return {"ok": True, "alert_removed": name}
