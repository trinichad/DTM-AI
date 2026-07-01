"""List Google Workspace ChromeOS devices via the Admin SDK Directory API (D-118) — read-only."""
from __future__ import annotations

from typing import Any

NAME = "gws_list_chromeos_devices"
DESCRIPTION = ("List the ChromeOS devices in a Google Workspace tenant (serial, model, status, "
               "annotated user/location, org unit, last sync). Use `query` to filter (e.g. "
               "\"status:active\", \"user:jane@acme.com\"). Scoped to the selected client; on 'All "
               "clients' (*) it aggregates across signed-in clients.")
SOURCE = "gws"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True

_PATH = "/admin/directory/v1/customer/my_customer/devices/chromeos"
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "optional device search, e.g. \"status:active\""},
    },
    "additionalProperties": False,
}


def _slim(d: dict) -> dict:
    out: dict[str, Any] = {
        "serial": d.get("serialNumber"),
        "model": d.get("model"),
        "status": d.get("status"),
        "user": d.get("annotatedUser"),
        "location": d.get("annotatedLocation"),
        "orgUnitPath": d.get("orgUnitPath"),
        "lastSync": d.get("lastSync"),
    }
    if d.get("tenant"):
        out["tenant"] = d["tenant"]
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def run(ctx, query: str = "", **_: Any):
    from ._gws_common import read_list
    base: dict[str, Any] = {"maxResults": 100, "projection": "BASIC", "orderBy": "serialNumber"}
    if (query or "").strip():
        base["query"] = query.strip()
    return read_list(ctx, _PATH, base, "chromeosdevices", _slim, out_key="chromeos_devices")
