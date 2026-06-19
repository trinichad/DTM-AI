"""List the client's Exchange retention policies (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_list_retention_policies"
DESCRIPTION = ("List the retention policies that exist in the client's Exchange Online — use "
               "this FIRST to see the valid choices before exo_set_retention_policy.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {},
                              "additionalProperties": False}


def run(ctx, **_: Any):
    from . import _exo_common as c
    r = ctx.client("exo").invoke("Get-RetentionPolicy")
    if c.err(r):
        return {"ok": False, "error": c.err(r)}
    rows = [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]
    policies = [{"name": p.get("Name"), "is_default": bool(p.get("IsDefault")),
                 "tags": p.get("RetentionPolicyTagLinks")} for p in rows]
    return {"count": len(policies), "policies": policies}
