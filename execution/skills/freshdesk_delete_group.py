"""Delete a Freshdesk group (D-83) — destructive."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_delete_group"
DESCRIPTION = ("Delete a Freshdesk group by `group_id` (agents are not deleted, just un-grouped). "
               "Destructive, so it always needs a per-action approval.")
SOURCE = "freshdesk"
GROUP = "freshdesk_team"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"group_id": {"type": "integer", "description": "the group id to delete"}},
    "required": ["group_id"],
    "additionalProperties": False,
}


def run(ctx, group_id: int, **_: Any):
    gid = int(group_id)
    r = ctx.client("freshdesk").write_destructive("DELETE", f"/groups/{gid}", None)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "group_id": gid, "note": "group deleted"}
