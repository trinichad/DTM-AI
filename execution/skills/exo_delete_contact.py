"""Delete a mail contact (D-65; SOP: exchange-online). The opposite of exo_create_contact."""
from __future__ import annotations

from typing import Any

NAME = "exo_delete_contact"
DESCRIPTION = ("DELETE an external mail CONTACT from the client's address book. Verifies it's "
               "gone before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "contact": {"type": "string",
                    "description": "the contact's name or external email address"},
    },
    "required": ["contact"],
    "additionalProperties": False,
}


def run(ctx, contact: str, **_: Any):
    from . import _exo_common as c
    contact = (contact or "").strip()
    exo = ctx.client("exo")
    cur = exo.invoke("Get-MailContact", {"Identity": contact})
    rows = [x for x in (cur if isinstance(cur, list) else [cur]) if isinstance(x, dict)]
    if c.err(cur) or not rows:
        return {"ok": True, "contact": contact, "note": "no such contact — nothing to delete"}
    name = str(rows[0].get("ExternalEmailAddress") or rows[0].get("Name") or contact)
    r = exo.invoke("Remove-MailContact", {"Identity": contact, "Confirm": False})
    if c.err(r):
        return {"ok": False, "step": "delete", "error": c.err(r)}
    check = exo.invoke("Get-MailContact", {"Identity": contact})
    rows2 = [x for x in (check if isinstance(check, list) else [check]) if isinstance(x, dict)]
    if not c.err(check) and rows2:
        return {"ok": False, "step": "verify",
                "error": f"Remove-MailContact returned no error but '{contact}' still "
                         f"exists — check Exchange directly"}
    return {"ok": True, "deleted": name}
