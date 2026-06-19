"""Search Freshdesk tickets with the filter query language (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_search_tickets"
DESCRIPTION = ("Search Freshdesk tickets with the filter query language. Pass `query` WITHOUT the "
               "outer quotes, e.g. \"priority:4 AND status:2\" or "
               "\"tag:'vip' AND created_at:>'2026-01-01'\". Supports fields like status, priority, "
               "agent_id, group_id, tag, type, created_at, updated_at, due_by. Returns matching "
               "tickets (max 30/page, up to ~300).")
SOURCE = "freshdesk"
GROUP = "freshdesk_tickets"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "the filter query (without the surrounding quotes)"},
    },
    "required": ["query"],
    "additionalProperties": False,
}


def run(ctx, query: str, **_: Any):
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "give a search query"}
    if len(q) > 512:
        return {"ok": False, "error": "query is too long"}
    out = []
    # search/tickets wraps results in {"results": [...], "total": N}; the client unwraps that.
    for t in ctx.client("freshdesk").get_paginated("/search/tickets", {"query": f'"{q}"'},
                                                   per_page=30, max_pages=10):
        out.append(_f.slim_ticket(t))
    return out
