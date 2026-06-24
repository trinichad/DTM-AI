"""Block / unblock / reconnect a UniFi client (D-84)."""
from __future__ import annotations

import re
from typing import Any

from . import _unifi_common as _u

NAME = "unifi_client_action"
DESCRIPTION = ("Perform an action on a connected UniFi client by `client_id`: `action`='block' "
               "(deny it network access), 'unblock' (restore access), 'reconnect' (kick it so it "
               "re-associates), or 'authorize' (grant a guest access). Pass `client_ids` (a list) "
               "to apply the same `action` to MANY clients in ONE call — do NOT call this tool once "
               "per client. Optional `site`.")
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
        "client_ids": {"type": "array", "items": {"type": "string"},
                       "description": "act on MANY clients in ONE call — a list of client ids; "
                                      "results come back together. Use this instead of calling the "
                                      "tool once per client."},
        "action": {"type": "string", "enum": list(_ACTIONS),
                   "description": "block / unblock / reconnect / authorize"},
        "site": {"type": "string", "description": "site name or id (optional)"},
    },
    "required": ["action"],
    "additionalProperties": False,
}


def _one(ctx, client_id: str, action: str, site: str) -> dict:
    cid = (client_id or "").strip()
    if not re.match(r"^[A-Za-z0-9:-]+$", cid):
        return {"ok": False, "client_id": cid, "error": "client_id is not valid"}
    act = _ACTIONS.get((action or "").strip().lower())
    if not act:
        return {"ok": False, "client_id": cid,
                "error": "action must be block, unblock, reconnect, or authorize"}
    client = ctx.client("unifi")
    sid, err = _u.resolve_site(client, site)
    if err:
        return {"ok": False, "client_id": cid, "error": err}
    r = client.write("POST", f"/v1/sites/{sid}/clients/{cid}/actions", {"action": act})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "client_id": cid, "error": r["error"]}
    return {"ok": True, "client_id": cid, "action": act, "note": "client action submitted"}


def run(ctx, client_id: str = "", action: str = "", site: str = "",
        client_ids: Any = None, **_: Any):
    wanted = [str(c).strip() for c in (client_ids or []) if str(c).strip()]
    if wanted:                                         # batch — same action, many clients
        results = [_one(ctx, c, action, site) for c in wanted[:200]]
        return {"ok": any(r.get("ok") for r in results), "devices_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, client_id, action, site)
