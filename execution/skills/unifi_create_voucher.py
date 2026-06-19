"""Create UniFi hotspot (guest WiFi) voucher(s) (D-84)."""
from __future__ import annotations

from typing import Any

from . import _unifi_common as _u

NAME = "unifi_create_voucher"
DESCRIPTION = ("Create guest-WiFi voucher code(s) on the UniFi hotspot. Give `time_limit_minutes` "
               "(how long each code is valid once used). Optional: count (how many codes, default "
               "1), name/note, guest_limit (devices per code, default 1), data_limit_mb, and "
               "down/up speed limits in Kbps. Optional `site`. Returns the new code(s).")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "time_limit_minutes": {"type": "integer", "minimum": 1, "maximum": 1000000,
                               "description": "minutes each voucher is valid once first used"},
        "count": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "how many codes (default 1)"},
        "name": {"type": "string", "description": "a note/name for the batch"},
        "guest_limit": {"type": "integer", "minimum": 1, "maximum": 100,
                        "description": "devices allowed per code (default 1)"},
        "data_limit_mb": {"type": "integer", "minimum": 1, "description": "data cap per code in MB (optional)"},
        "down_kbps": {"type": "integer", "minimum": 1, "description": "download speed limit Kbps (optional)"},
        "up_kbps": {"type": "integer", "minimum": 1, "description": "upload speed limit Kbps (optional)"},
        "site": {"type": "string", "description": "site name or id (optional)"},
    },
    "required": ["time_limit_minutes"],
    "additionalProperties": False,
}


def run(ctx, time_limit_minutes: int, count: Any = None, name: str = "", guest_limit: Any = None,
        data_limit_mb: Any = None, down_kbps: Any = None, up_kbps: Any = None, site: str = "",
        **_: Any):
    client = ctx.client("unifi")
    sid, err = _u.resolve_site(client, site)
    if err:
        return {"ok": False, "error": err}
    body: dict[str, Any] = {
        "timeLimitMinutes": int(time_limit_minutes),
        "count": int(count) if count is not None else 1,
        "authorizedGuestLimit": int(guest_limit) if guest_limit is not None else 1,
    }
    if (name or "").strip():
        body["name"] = name.strip()[:128]
    if data_limit_mb is not None:
        body["dataUsageLimitMBytes"] = int(data_limit_mb)
    if down_kbps is not None:
        body["rxRateLimitKbps"] = int(down_kbps)
    if up_kbps is not None:
        body["txRateLimitKbps"] = int(up_kbps)
    r = client.write("POST", f"/v1/sites/{sid}/hotspot/vouchers", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "vouchers": r, "note": "voucher(s) created — read codes with unifi_list_vouchers"}
