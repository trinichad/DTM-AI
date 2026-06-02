"""kb_search — search the DTM AI knowledge base (Obsidian-style markdown vault)."""
from __future__ import annotations

from typing import Any

from execution.core.memory import VaultStore

NAME = "kb_search"
DESCRIPTION = ("Search DTM internal knowledge base / runbooks / SOPs for this question. "
               "Returns matching doc paths + snippets. Use for 'how do we…' procedure questions.")
SOURCE = "dtm_ai"
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
    hits = VaultStore().search_kb(query, limit=max(1, min(limit, 20)))
    return {"query": query, "matches": hits, "count": len(hits)}
