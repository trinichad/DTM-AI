"""Merge Freshdesk tickets into a primary (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_merge_tickets"
DESCRIPTION = ("Merge one or more Freshdesk tickets INTO a primary ticket — for de-duplicating the "
               "same issue reported twice. Give the `primary_id` (the one to keep) and "
               "`ticket_ids` (the ones folded into it). The merged tickets are closed and their "
               "conversations move to the primary.")
SOURCE = "freshdesk"
GROUP = "freshdesk_tickets"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "primary_id": {"type": "integer", "description": "the ticket to keep"},
        "ticket_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1,
                       "description": "the ticket(s) to merge into the primary"},
        "note": {"type": "string", "description": "optional note added to the merged tickets"},
    },
    "required": ["primary_id", "ticket_ids"],
    "additionalProperties": False,
}


def run(ctx, primary_id: int, ticket_ids: Any, note: str = "", **_: Any):
    pid = int(primary_id)
    ids = [int(i) for i in ticket_ids if str(i).strip()] if isinstance(ticket_ids, list) else []
    ids = [i for i in ids if i != pid]
    if not ids:
        return {"ok": False, "error": "give at least one ticket id to merge (other than the primary)"}
    payload: dict[str, Any] = {"primary_id": pid, "ticket_ids": ids}
    if (note or "").strip():
        payload["note_in_primary"] = {"body": note.strip(), "private": True}
    r = ctx.client("freshdesk").write("POST", "/tickets/merge", payload)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "primary_id": pid, "merged": ids, "note": "tickets merged"}
