"""Promote a Freshdesk contact to an agent (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_make_agent"
DESCRIPTION = ("Convert a Freshdesk contact into an AGENT (gives them helpdesk login/access) by "
               "`contact_id`. Note: this consumes an agent license/seat. Confirm the contact "
               "first with freshdesk_get_contact.")
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "write"
RISK_LEVEL = "high"          # consumes a paid agent seat + grants access
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "contact_id": {"type": "integer", "description": "the contact to promote"},
    },
    "required": ["contact_id"],
    "additionalProperties": False,
}


def run(ctx, contact_id: int, **_: Any):
    cid = int(contact_id)
    r = ctx.client("freshdesk").write("PUT", f"/contacts/{cid}/make_agent", None)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "contact_id": cid, "agent": r, "note": "contact promoted to agent"}
