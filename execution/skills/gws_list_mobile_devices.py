"""List Google Workspace managed mobile devices via the Admin SDK Directory API (D-118) — read-only."""
from __future__ import annotations

from typing import Any

NAME = "gws_list_mobile_devices"
DESCRIPTION = ("List the mobile devices enrolled in a Google Workspace tenant (owner email, model, "
               "OS, type, management status, last sync). Use `query` to filter (e.g. \"email:jane@"
               "acme.com\", \"status:approved\"). Scoped to the selected client; on 'All clients' (*) "
               "it aggregates across signed-in clients.")
SOURCE = "gws"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True

_PATH = "/admin/directory/v1/customer/my_customer/devices/mobile"
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "optional device search, e.g. \"email:jane@acme.com\""},
    },
    "additionalProperties": False,
}


def _slim(d: dict) -> dict:
    emails = d.get("email") or []
    out: dict[str, Any] = {
        "owner": emails[0] if isinstance(emails, list) and emails else None,
        "model": d.get("model") or d.get("name"),
        "os": d.get("os"),
        "type": d.get("type"),
        "status": d.get("status"),
        "lastSync": d.get("lastSync"),
        "resourceId": d.get("resourceId"),          # needed by gws_wipe_mobile_device
    }
    if d.get("tenant"):
        out["tenant"] = d["tenant"]
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def run(ctx, query: str = "", **_: Any):
    from ._gws_common import read_list
    base: dict[str, Any] = {"maxResults": 100, "projection": "BASIC", "orderBy": "email"}
    if (query or "").strip():
        base["query"] = query.strip()
    return read_list(ctx, _PATH, base, "mobiledevices", _slim, out_key="mobile_devices")
