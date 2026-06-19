"""List Exchange Online mailboxes for the bound client (D-41; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_list_mailboxes"
DESCRIPTION = ("List the client's Exchange Online mailboxes (display name, address, type). "
               "Pass type='shared' for ONLY shared mailboxes (or 'user'/'room'). Pass "
               "`identity` (name or address) to fetch one. Use this to verify mailbox "
               "changes. Needs the client's Exchange connection.")
SOURCE = "m365"              # grouped with the other Office 365 tools; client is ctx.client("exo")
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "one mailbox by name/address (optional)"},
        "type": {"type": "string", "enum": ["all", "shared", "user", "room"],
                 "description": "filter by mailbox type (default all)"},
        "limit": {"type": "integer", "description": "max results (default 100, max 500)"},
    },
    "additionalProperties": False,
}

_TYPES = {"shared": "SharedMailbox", "user": "UserMailbox", "room": "RoomMailbox"}


def run(ctx, identity: str = "", type: str = "all", limit: int = 100, **_: Any):
    limit = max(1, min(int(limit or 100), 500))
    params: dict[str, Any] = {"ResultSize": limit}
    details = _TYPES.get((type or "all").strip().lower())
    if details:
        params["RecipientTypeDetails"] = details
    if (identity or "").strip():
        params["Identity"] = identity.strip()
    r = ctx.client("exo").invoke("Get-Mailbox", params)
    if isinstance(r, dict) and r.get("error"):
        err = str(r["error"])
        # A lookup for one identity that doesn't exist is a clean "0 results", not an error —
        # Exchange returns 404 ManagementObjectNotFound for Get-Mailbox -Identity <missing>.
        if "NotFound" in err or "couldn't be found" in err or "ManagementObjectNotFound" in err:
            return {"count": 0, "mailboxes": [],
                    "note": f"no mailbox matching '{identity}'" if identity.strip()
                            else "no mailboxes found"}
        return {"ok": False, "error": err}
    rows = r if isinstance(r, list) else ([r] if isinstance(r, dict) else [])
    slim = [{"display_name": m.get("DisplayName"),
             "email": m.get("PrimarySmtpAddress"),
             "type": m.get("RecipientTypeDetails")} for m in rows if isinstance(m, dict)]
    return {"count": len(slim), "mailboxes": slim}
