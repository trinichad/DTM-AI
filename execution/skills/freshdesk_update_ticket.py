"""Update a Freshdesk ticket — status, priority, assignment, etc. (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_update_ticket"
DESCRIPTION = ("Update a Freshdesk ticket by `ticket_id`. Change any of: status (open/pending/"
               "resolved/closed), priority (low/medium/high/urgent), type, group_id, agent_id "
               "(responder), tags. Only the fields you pass change. To reply to the customer use "
               "freshdesk_reply_ticket; to add an internal note use freshdesk_add_note. Pass "
               "`ticket_id` for one or `ticket_ids` (a list) to apply the SAME change to MANY in "
               "ONE call — do NOT call this tool once per ticket.")
SOURCE = "freshdesk"
GROUP = "freshdesk_tickets"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticket_id": {"type": "integer", "description": "the Freshdesk ticket id"},
        "ticket_ids": {"type": "array", "items": {"type": "integer"},
                       "description": "apply the SAME change to MANY tickets in ONE call — a list "
                                      "of ticket ids; results come back together. Use this instead "
                                      "of calling the tool once per ticket."},
        "status": {"type": "string", "enum": ["open", "pending", "resolved", "closed"]},
        "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
        "type": {"type": "string"},
        "group_id": {"type": "integer"},
        "agent_id": {"type": "integer", "description": "responder/agent to assign"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}


def run(ctx, ticket_id: Any = None, ticket_ids: Any = None, status: str = "", priority: str = "",
        type: str = "", group_id: Any = None, agent_id: Any = None, tags: Any = None, **_: Any):
    wanted = [int(x) for x in (ticket_ids or [])]
    if wanted:                                         # batch (D-110) — one call, many tickets
        results = [_one(ctx, t, status, priority, type, group_id, agent_id, tags)
                   for t in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "tickets_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, ticket_id, status, priority, type, group_id, agent_id, tags)


def _one(ctx, ticket_id: int, status: str = "", priority: str = "", type: str = "",
         group_id: Any = None, agent_id: Any = None, tags: Any = None) -> dict:
    tid = int(ticket_id)
    body: dict[str, Any] = {}
    if (status or "").strip():
        body["status"] = _f.status_id(status)
    if (priority or "").strip():
        body["priority"] = _f.priority_id(priority)
    if (type or "").strip():
        body["type"] = type.strip()
    if group_id is not None:
        body["group_id"] = int(group_id)
    if agent_id is not None:
        body["responder_id"] = int(agent_id)
    if isinstance(tags, list):
        body["tags"] = [str(t).strip() for t in tags if str(t or "").strip()]
    if not body:
        return {"ok": False, "ticket_id": tid, "error": "give at least one field to change"}
    r = ctx.client("freshdesk").write("PUT", f"/tickets/{tid}", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "ticket_id": tid, "error": r["error"]}
    return {"ok": True, "ticket_id": tid,
            "ticket": _f.slim_ticket(r) if isinstance(r, dict) else r,
            "note": "ticket updated"}
