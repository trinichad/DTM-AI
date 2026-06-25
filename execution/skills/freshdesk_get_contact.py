"""Get one Freshdesk contact's detail (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_get_contact"
DESCRIPTION = ("Get the full detail of one Freshdesk contact by `contact_id`. Pass `contact_id` "
               "for one or `contact_ids` (a list) to fetch MANY in ONE call — do NOT call this "
               "tool once per contact.")
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "contact_id": {"type": "integer", "description": "the Freshdesk contact id"},
        "contact_ids": {"type": "array", "items": {"type": "integer"},
                        "description": "fetch MANY contacts in ONE call — a list of contact ids; "
                                       "results come back together. Use this instead of calling "
                                       "the tool once per contact."},
    },
    "additionalProperties": False,
}


def run(ctx, contact_id: Any = None, contact_ids: Any = None, **_: Any):
    wanted = [int(x) for x in (contact_ids or [])]
    if wanted:                                         # batch (D-110) — one call, many contacts
        results = ctx.map_progress(wanted[:500], lambda c: _one(ctx, c))
        return {"ok": any(r.get("ok") for r in results), "contacts_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, contact_id)


def _one(ctx, contact_id: int) -> dict:
    return ctx.client("freshdesk").get(f"/contacts/{int(contact_id)}")
