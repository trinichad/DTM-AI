"""Delete a Freshdesk contact (D-83) — destructive."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_delete_contact"
DESCRIPTION = ("Delete a Freshdesk contact by `contact_id` (soft delete — it can be restored from "
               "Freshdesk). Destructive, so it always needs a per-action approval.")
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"contact_id": {"type": "integer", "description": "the contact id to delete"}},
    "required": ["contact_id"],
    "additionalProperties": False,
}


def run(ctx, contact_id: int, **_: Any):
    cid = int(contact_id)
    r = ctx.client("freshdesk").write_destructive("DELETE", f"/contacts/{cid}", None)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "contact_id": cid, "note": "contact deleted"}
