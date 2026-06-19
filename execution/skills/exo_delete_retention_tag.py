"""Delete a retention tag (D-65; SOP: exchange-online). Opposite of exo_create_retention_tag."""
from __future__ import annotations

from typing import Any

NAME = "exo_delete_retention_tag"
DESCRIPTION = ("DELETE a retention TAG. Removing a tag also removes it from any policy that "
               "uses it. Verifies it's gone before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string", "description": "the retention tag's exact name"}},
    "required": ["name"],
    "additionalProperties": False,
}


def run(ctx, name: str, **_: Any):
    from . import _exo_common as c
    name = (name or "").strip()
    exo = ctx.client("exo")
    cur = exo.invoke("Get-RetentionPolicyTag", {"Identity": name})
    rows = [x for x in (cur if isinstance(cur, list) else [cur]) if isinstance(x, dict)]
    if c.err(cur) or not rows:
        return {"ok": True, "name": name, "note": "no such retention tag — nothing to delete"}
    r = exo.invoke("Remove-RetentionPolicyTag", {"Identity": name, "Confirm": False})
    if c.err(r):
        return {"ok": False, "step": "delete", "error": c.err(r)}
    check = exo.invoke("Get-RetentionPolicyTag", {"Identity": name})
    rows2 = [x for x in (check if isinstance(check, list) else [check]) if isinstance(x, dict)]
    if not c.err(check) and rows2:
        return {"ok": False, "step": "verify",
                "error": f"the tag '{name}' still exists after delete — check Exchange"}
    return {"ok": True, "deleted": name}
