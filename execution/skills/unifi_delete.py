"""unifi_delete — generic bounded DELETE for advanced UniFi config (D-84) — destructive."""
from __future__ import annotations

from typing import Any

NAME = "unifi_delete"
DESCRIPTION = ("ADVANCED: delete a UniFi config object the dedicated tools don't cover — a network/"
               "VLAN, WiFi SSID, firewall zone/policy, DNS policy, ACL rule, or traffic-matching "
               "list. Give the API `path` (e.g. /v1/sites/{siteId}/firewall/policies/{id}). Only "
               "allow-listed delete paths are permitted. Destructive, so it always needs a "
               "per-action approval and can never be batch-approved.")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "the UniFi API path of the object to delete"},
    },
    "required": ["path"],
    "additionalProperties": False,
}


def run(ctx, path: str, **_: Any):
    p = (path or "").strip()
    if not p.startswith("/v1/sites/") or "://" in p or ".." in p:
        return {"ok": False, "error": "path must be a simple /v1/sites/... UniFi API path"}
    r = ctx.client("unifi").write_destructive("DELETE", p, None)   # client DESTRUCTIVE_RULES enforce
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "path": p, "note": "object deleted"}
