"""List Google Workspace Shared Drives via the Drive API (domain-admin access) — read-only (D-118)."""
from __future__ import annotations

from typing import Any

NAME = "gws_list_shared_drives"
DESCRIPTION = ("List the Shared Drives in a Google Workspace tenant (id, name, created time). Uses "
               "domain-admin access so it sees every shared drive, not just the admin's. Scoped to "
               "the selected client; on 'All clients' (*) it aggregates across signed-in clients.")
SOURCE = "gws"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True

_PATH = "/drive/v3/drives"
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string",
                  "description": "optional Drive query, e.g. \"name contains 'Finance'\""},
    },
    "additionalProperties": False,
}


def _slim(d: dict) -> dict:
    out: dict[str, Any] = {"id": d.get("id"), "name": d.get("name")}
    for k in ("createdTime", "tenant"):
        if d.get(k):
            out[k] = d[k]
    return out


def run(ctx, query: str = "", **_: Any):
    from ._gws_common import read_list
    base: dict[str, Any] = {"useDomainAdminAccess": "true", "pageSize": 100,
                            "fields": "nextPageToken,drives(id,name,createdTime)"}
    if (query or "").strip():
        base["q"] = query.strip()
    return read_list(ctx, _PATH, base, "drives", _slim, out_key="shared_drives")
