"""Delete a Freshdesk company (D-83) — destructive."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_delete_company"
DESCRIPTION = ("Delete a Freshdesk company by `company_id` (its contacts are NOT deleted, just "
               "un-associated). Destructive, so it always needs a per-action approval.")
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"company_id": {"type": "integer", "description": "the company id to delete"}},
    "required": ["company_id"],
    "additionalProperties": False,
}


def run(ctx, company_id: int, **_: Any):
    cid = int(company_id)
    r = ctx.client("freshdesk").write_destructive("DELETE", f"/companies/{cid}", None)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "company_id": cid, "note": "company deleted"}
