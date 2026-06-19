"""List Freshdesk tickets, filtered (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_list_tickets"
DESCRIPTION = ("List Freshdesk tickets (newest first). Optional filters: status (open/pending/"
               "resolved/closed), priority (low/medium/high/urgent), requester_id, company_id, "
               "group_id, agent_id (responder), and updated_since (ISO date). For complex queries "
               "use freshdesk_search_tickets. Returns trimmed ticket fields with status/priority "
               "as words.")
SOURCE = "freshdesk"
GROUP = "freshdesk_tickets"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["open", "pending", "resolved", "closed"]},
        "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
        "requester_id": {"type": "integer", "description": "filter by requester (contact) id"},
        "company_id": {"type": "integer", "description": "filter by company id"},
        "group_id": {"type": "integer", "description": "filter by group id"},
        "agent_id": {"type": "integer", "description": "filter by assigned agent (responder) id"},
        "updated_since": {"type": "string", "description": "ISO-8601 date; only tickets updated after"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 300, "description": "max tickets (default 100)"},
    },
    "additionalProperties": False,
}


def run(ctx, status: str = "", priority: str = "", requester_id: Any = None, company_id: Any = None,
        group_id: Any = None, agent_id: Any = None, updated_since: str = "", limit: Any = None,
        **_: Any):
    params: dict[str, Any] = {"order_by": "updated_at", "order_type": "desc"}
    if status:
        params["status"] = _f.status_id(status)
    if priority:
        params["priority"] = _f.priority_id(priority)
    if requester_id is not None:
        params["requester_id"] = int(requester_id)
    if company_id is not None:
        params["company_id"] = int(company_id)
    if group_id is not None:
        params["group_id"] = int(group_id)
    if agent_id is not None:
        params["agent_id"] = int(agent_id)
    if (updated_since or "").strip():
        params["updated_since"] = updated_since.strip()
    cap = 100
    if limit is not None:
        cap = max(1, min(300, int(limit)))
    out = []
    for t in ctx.client("freshdesk").get_paginated("/tickets", params):
        out.append(_f.slim_ticket(t))
        if len(out) >= cap:
            break
    return out
