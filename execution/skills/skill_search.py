"""skill_search — find a saved learned-skill PLAYBOOK before re-deriving a procedure (D-15).

Read-only: composes nothing, touches no client system — it just looks up reusable procedures the
owner has saved, so the agent reuses a known sequence of (already-trusted) tools instead of
re-figuring it out each time.
"""
from __future__ import annotations

from typing import Any

from execution.core.playbooks import PlaybookStore

NAME = "skill_search"
DESCRIPTION = ("Search your saved learned skills (reusable playbooks composed from existing tools). "
               "Call this BEFORE a multi-step task to reuse a known procedure instead of re-deriving "
               "it. Returns matching skills with their steps.")
SOURCE = "msp_ai"
CATEGORY = "read"
RISK_LEVEL = "none"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "limit": {"type": "integer"},
    },
    "required": ["query"],
    "additionalProperties": False,
}


def run(ctx, query: str, limit: int = 5, **_: Any):
    hits = PlaybookStore().search(query, limit=max(1, min(limit, 20)))
    return {"query": query, "matches": hits, "count": len(hits)}
