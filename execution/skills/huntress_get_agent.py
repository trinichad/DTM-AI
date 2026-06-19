"""Get one Huntress agent's detail (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "huntress_get_agent"
DESCRIPTION = ("Get the full detail for one Huntress agent by `agent_id` — OS, version, last "
               "seen/callback, organization, and platform specifics.")
SOURCE = "huntress"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agent_id": {"type": "string", "description": "the Huntress agent id"},
    },
    "required": ["agent_id"],
    "additionalProperties": False,
}


def run(ctx, agent_id: str, **_: Any):
    aid = str(agent_id or "").strip()
    if not re.match(r"^\d+$", aid):
        return {"ok": False, "error": "agent_id must be numeric"}
    return ctx.client("huntress").get(f"/agents/{aid}")
