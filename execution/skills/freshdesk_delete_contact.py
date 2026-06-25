"""Delete a Freshdesk contact (D-83) — destructive."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_delete_contact"
DESCRIPTION = ("Delete a Freshdesk contact by `contact_id` (soft delete — it can be restored from "
               "Freshdesk). Destructive, so it always needs a per-action approval. Pass "
               "`contact_id` for one or `contact_ids` (a list) to delete MANY in ONE call — do "
               "NOT call this tool once per contact.")
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "contact_id": {"type": "integer", "description": "the contact id to delete"},
        "contact_ids": {"type": "array", "items": {"type": "integer"},
                        "description": "delete MANY contacts in ONE call — a list of contact ids; "
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
    cid = int(contact_id)
    r = ctx.client("freshdesk").write_destructive("DELETE", f"/contacts/{cid}", None)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "contact_id": cid, "error": r["error"]}
    return {"ok": True, "contact_id": cid, "note": "contact deleted"}
