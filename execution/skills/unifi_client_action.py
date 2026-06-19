"""Block / unblock / reconnect a UniFi client (D-84)."""
from __future__ import annotations

import re
from typing import Any

from . import _unifi_common as _u

NAME = "unifi_client_action"
DESCRIPTION = ("Perform an action on a connected UniFi client by `client_id`: `action`='block' "
               "(deny it network access), 'unblock' (restore access), 'reconnect' (kick it so it "
               "re-associates), or 'authorize' (grant a guest access). Optional `site`.")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_ACTIONS = {"block": "BLOCK", "unblock": "UNBLOCK", "reconnect": "RECONNECT",
            "authorize": "AUTHORIZE_GUEST"}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "client_id": {"type": "string", "description": "the UniFi client id"},
        "action": {"type": "string", "enum": list(_ACTIONS),
                   "description": "block / unblock / reconnect / authorize"},
        "site": {"type": "string", "description": "site name or id (optional)"},
    },
    "required": ["client_id", "action"],
    "additionalProperties": False,
}


def run(ctx, client_id: str, action: str, site: str = "", **_: Any):
    cid = (client_id or "").strip()
    if not re.match(r"^[A-Za-z0-9:-]+$", cid):
        return {"ok": False, "error": "client_id is not valid"}
    act = _ACTIONS.get((action or "").strip().lower())
    if not act:
        return {"ok": False, "error": "action must be block, unblock, reconnect, or authorize"}
    client = ctx.client("unifi")
    sid, err = _u.resolve_site(client, site)
    if err:
        return {"ok": False, "error": err}
    r = client.write("POST", f"/v1/sites/{sid}/clients/{cid}/actions", {"action": act})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "client_id": cid, "action": act, "note": "client action submitted"}
