"""Remove the auto-forwarding block rule (D-65; SOP: exchange-online).
The opposite of exo_block_auto_forwarding."""
from __future__ import annotations

from typing import Any

NAME = "exo_unblock_auto_forwarding"
DESCRIPTION = ("Remove the transport rule that blocks auto-forwarding to external domains "
               "(created by exo_block_auto_forwarding) — auto-forwarding becomes allowed again. "
               "Verifies the rule is gone before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"           # re-enabling external forwarding is a data-exfiltration loosening
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_DEFAULT_NAME = "Prevent auto forwarding of email to external domains"
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string",
                 "description": f"the rule name to remove (default: '{_DEFAULT_NAME}')"},
    },
    "additionalProperties": False,
}


def run(ctx, name: str = "", **_: Any):
    from . import _exo_common as c
    name = (name or "").strip() or _DEFAULT_NAME
    exo = ctx.client("exo")
    cur = exo.invoke("Get-TransportRule", {"Identity": name})
    rows = [x for x in (cur if isinstance(cur, list) else [cur]) if isinstance(x, dict)]
    if c.err(cur) or not rows:
        return {"ok": True, "rule": name, "note": "no such transport rule — nothing to remove"}
    r = exo.invoke("Remove-TransportRule", {"Identity": name, "Confirm": False})
    if c.err(r):
        return {"ok": False, "step": "remove", "error": c.err(r)}
    check = exo.invoke("Get-TransportRule", {"Identity": name})
    rows2 = [x for x in (check if isinstance(check, list) else [check]) if isinstance(x, dict)]
    if not c.err(check) and rows2:
        return {"ok": False, "step": "verify",
                "error": f"the rule '{name}' still exists after removal — check Exchange"}
    return {"ok": True, "rule_removed": name,
            "note": "auto-forwarding to external domains is allowed again (can take ~30 min)"}
