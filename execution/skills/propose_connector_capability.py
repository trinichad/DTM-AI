"""Request a new connector command (cmdlet) be added to the safe-list (D-64; SOP: self-development).

When a task needs an Exchange cmdlet that isn't yet available, the agent proposes it here. This is
a CATEGORY=write tool, so dispatch pauses it for the owner's approval — the owner sees the exact
cmdlet + params + reason. On approval, run() persists the grant and the cmdlet becomes usable by
the EXO connector (still subject to every per-tool enable/allow_write/approval gate). It can NEVER
add a destructive (data-deleting) cmdlet — those stay hand-written.
"""
from __future__ import annotations

from typing import Any

NAME = "propose_connector_capability"
DESCRIPTION = ("Request that a NEW Exchange command (cmdlet) be added to the connector's "
               "safe-list, when a task needs one that isn't available yet (e.g. a Get-/Set-/"
               "Add-/Remove- cmdlet the connector currently refuses). The owner approves the "
               "exact cmdlet before it can be used. Give the connector ('exo'), the exact "
               "cmdlet name, kind ('read' or 'write'), the parameter names the cmdlet needs, "
               "and a short reason. CANNOT add destructive/data-deleting cmdlets. After it's "
               "approved, build or run the tool that uses it.")
SOURCE = "system"            # NOT msp_ai — must take the normal approval gate, not the
CATEGORY = "write"           # own-vault-write auto-run floor (this changes connector config)
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "connector": {"type": "string", "enum": ["exo"],
                      "description": "the connector to extend (currently 'exo' = Exchange)"},
        "cmdlet": {"type": "string",
                   "description": "exact cmdlet name, e.g. 'Set-CASMailbox'"},
        "kind": {"type": "string", "enum": ["read", "write"],
                 "description": "'read' (Get-*) or 'write' (changes config). Never destructive."},
        "params": {"type": "array", "items": {"type": "string"},
                   "description": "the parameter names the cmdlet needs, e.g. "
                                  "['Identity','OWAEnabled'] — only these will be allowed"},
        "reason": {"type": "string",
                   "description": "why it's needed / what task it unblocks"},
    },
    "required": ["connector", "cmdlet", "kind"],
    "additionalProperties": False,
}


def run(ctx, connector: str, cmdlet: str, kind: str = "write", params: Any = None,
        reason: str = "", **_: Any):
    from ..core import connector_grants
    ok, why = connector_grants.can_grant(connector, cmdlet, kind)
    if not ok:
        return {"ok": False, "error": why}
    plist = [str(p) for p in params if str(p or "").strip()] if isinstance(params, list) else []
    actor = getattr(ctx, "actor", "") or ""
    res = connector_grants.add(connector, cmdlet, kind, plist,
                               reason=reason, by=actor)
    if not res.get("ok"):
        return res
    return {"ok": True, "granted": cmdlet, "connector": res["connector"], "kind": res["kind"],
            "params_allowed": res["params"],
            "note": f"'{cmdlet}' is now on the Exchange safe-list. Tools may use it via "
                    f"ctx.client(\"exo\").invoke(\"{cmdlet}\", {{...}}) — still gated by enable + "
                    f"allow_write + per-run approval. Revoke any time from the Capabilities tab. "
                    f"Now build or run the tool that needs it."}
