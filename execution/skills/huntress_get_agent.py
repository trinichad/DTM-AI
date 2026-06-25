"""Get one Huntress agent's detail (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "huntress_get_agent"
DESCRIPTION = ("Get the full detail for one Huntress agent by `agent_id` — OS, version, last "
               "seen/callback, organization, and platform specifics. Pass `agent_id` for one "
               "agent or `agent_ids` (a list) to fetch MANY in ONE call — do NOT call this tool "
               "once per agent.")
SOURCE = "huntress"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agent_id": {"type": "string", "description": "the Huntress agent id"},
        "agent_ids": {"type": "array", "items": {"type": "string"},
                      "description": "fetch MANY agents in ONE call — a list of agent ids; results "
                                     "come back together. Use this instead of calling the tool once "
                                     "per agent."},
    },
    "additionalProperties": False,
}


def run(ctx, agent_id: str = "", agent_ids: Any = None, **_: Any):
    wanted = [str(x).strip() for x in (agent_ids or []) if str(x).strip()]
    if wanted:                                         # batch (D-110) — one call, many agents
        results = ctx.map_progress(wanted[:500], lambda x: _one(ctx, x))
        return {"ok": any(r.get("ok") for r in results), "agents_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, agent_id)


def _one(ctx, agent_id: str) -> dict:
    aid = str(agent_id or "").strip()
    if not re.match(r"^\d+$", aid):
        return {"ok": False, "agent_id": aid, "error": "agent_id must be numeric"}
    return ctx.client("huntress").get(f"/agents/{aid}")
