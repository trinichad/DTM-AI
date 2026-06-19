"""Delete a UniFi hotspot voucher (D-84) — destructive."""
from __future__ import annotations

import re
from typing import Any

from . import _unifi_common as _u

NAME = "unifi_delete_voucher"
DESCRIPTION = ("Delete (revoke) a UniFi guest-WiFi voucher by `voucher_id` — the code stops "
               "working. Destructive, so it always needs a per-action approval. Optional `site`.")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "destructive"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "voucher_id": {"type": "string", "description": "the voucher id to delete"},
        "site": {"type": "string", "description": "site name or id (optional)"},
    },
    "required": ["voucher_id"],
    "additionalProperties": False,
}


def run(ctx, voucher_id: str, site: str = "", **_: Any):
    vid = (voucher_id or "").strip()
    if not re.match(r"^[A-Za-z0-9-]+$", vid):
        return {"ok": False, "error": "voucher_id is not valid"}
    client = ctx.client("unifi")
    sid, err = _u.resolve_site(client, site)
    if err:
        return {"ok": False, "error": err}
    r = client.write_destructive("DELETE", f"/v1/sites/{sid}/hotspot/vouchers/{vid}", None)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "voucher_id": vid, "note": "voucher deleted"}
