"""Create a Freshdesk group (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_create_group"
DESCRIPTION = ("Create a Freshdesk group (a routing team). Give the `name`. Optional: description "
               "and agent_ids (the agents to add).")
SOURCE = "freshdesk"
GROUP = "freshdesk_team"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "the group name"},
        "description": {"type": "string"},
        "agent_ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["name"],
    "additionalProperties": False,
}


def run(ctx, name: str, description: str = "", agent_ids: Any = None, **_: Any):
    nm = (name or "").strip()
    if not nm:
        return {"ok": False, "error": "give the group name"}
    body: dict[str, Any] = {"name": nm[:255]}
    if (description or "").strip():
        body["description"] = description.strip()
    if isinstance(agent_ids, list) and agent_ids:
        body["agent_ids"] = [int(a) for a in agent_ids]
    r = ctx.client("freshdesk").write("POST", "/groups", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "group": r, "note": "group created"}
