"""Create a mail contact — an external person in the address book (D-56; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_create_contact"
DESCRIPTION = ("Add a CONTACT to the client's Exchange — an EXTERNAL person (their address is "
               "outside the client's domains) who then shows up in the Global Address List "
               "and can be added to distribution groups. Give their name and external email. "
               "Verifies before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "display name, e.g. 'Jane Vendor'"},
        "email": {"type": "string", "description": "their EXTERNAL email address"},
        "first_name": {"type": "string", "description": "first name (optional)"},
        "last_name": {"type": "string", "description": "last name (optional)"},
    },
    "required": ["name", "email"],
    "additionalProperties": False,
}


def run(ctx, name: str, email: str, first_name: str = "", last_name: str = "", **_: Any):
    from . import _exo_common as c
    name = (name or "").strip()
    email = (email or "").strip().lower()
    if not name:
        return {"ok": False, "error": "the contact needs a name"}
    if "@" not in email or " " in email:
        return {"ok": False, "error": f"'{email}' is not a valid email address"}
    exo = ctx.client("exo")

    existing = exo.invoke("Get-MailContact", {"Identity": email})
    if not c.err(existing):
        return {"ok": False, "error": f"a contact with the address '{email}' already exists"}

    params: dict[str, Any] = {"Name": name, "DisplayName": name,
                              "ExternalEmailAddress": email, "Confirm": False}
    if (first_name or "").strip():
        params["FirstName"] = first_name.strip()
    if (last_name or "").strip():
        params["LastName"] = last_name.strip()
    r = exo.invoke("New-MailContact", params)
    if c.err(r):
        return {"ok": False, "step": "create", "error": c.err(r)}

    check = exo.invoke("Get-MailContact", {"Identity": email})
    rows = [x for x in (check if isinstance(check, list) else [check]) if isinstance(x, dict)]
    if c.err(check) or not rows:
        return {"ok": False, "step": "verify",
                "error": f"New-MailContact returned no error but '{email}' could not be "
                         f"read back — check Exchange directly"}
    return {"ok": True, "created": name, "email": email,
            "note": "the contact appears in the address book and can join distribution "
                    "groups via exo_add_group_member"}
