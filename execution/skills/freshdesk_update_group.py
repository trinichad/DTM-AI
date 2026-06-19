"""Update a Freshdesk group (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_update_group"
DESCRIPTION = ("Update a Freshdesk group by `group_id` — name, description, or its agent_ids "
               "(replaces the membership). Only the fields you pass change.")
SOURCE = "freshdesk"
GROUP = "freshdesk_team"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "group_id": {"type": "integer", "description": "the Freshdesk group id"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "agent_ids": {"type": "array", "items": {"type": "integer"},
                      "description": "replaces the group's agent membership"},
    },
    "required": ["group_id"],
    "additionalProperties": False,
}


def run(ctx, group_id: int, name: str = "", description: str = "", agent_ids: Any = None, **_: Any):
    body: dict[str, Any] = {}
    if (name or "").strip():
        body["name"] = name.strip()[:255]
    if (description or "").strip():
        body["description"] = description.strip()
    if isinstance(agent_ids, list):
        body["agent_ids"] = [int(a) for a in agent_ids]
    if not body:
        return {"ok": False, "error": "give at least one field to change"}
    r = ctx.client("freshdesk").write("PUT", f"/groups/{int(group_id)}", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "group": r, "note": "group updated"}
