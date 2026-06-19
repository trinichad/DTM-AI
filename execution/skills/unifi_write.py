"""unifi_write — generic bounded write for advanced UniFi config (D-84)."""
from __future__ import annotations

from typing import Any

NAME = "unifi_write"
DESCRIPTION = ("ADVANCED: create or update UniFi network config the dedicated tools don't cover — "
               "networks/VLANs, WiFi SSIDs, firewall zones & policies, DNS policies, ACL rules, "
               "traffic-matching lists, policy ordering. Give the `method` (POST to create, PUT/"
               "PATCH to update), the API `path` (e.g. /v1/sites/{siteId}/firewall/policies), and "
               "the JSON `body`. Only allow-listed config paths are permitted; the exact command "
               "appears on the approval card. Use unifi_list_sites for the siteId, and "
               "unifi_read to see an existing object's shape before editing.")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_METHODS = ("POST", "PUT", "PATCH")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "method": {"type": "string", "enum": list(_METHODS)},
        "path": {"type": "string", "description": "the UniFi API path, starting with /v1/sites/"},
        "body": {"type": "object", "description": "the JSON request body"},
    },
    "required": ["method", "path", "body"],
    "additionalProperties": False,
}


def run(ctx, method: str, path: str, body: dict, **_: Any):
    m = (method or "").strip().upper()
    p = (path or "").strip()
    if m not in _METHODS:
        return {"ok": False, "error": "method must be POST, PUT, or PATCH"}
    if not p.startswith("/v1/") or "://" in p or ".." in p:
        return {"ok": False, "error": "path must be a simple /v1/... UniFi API path"}
    if not isinstance(body, dict):
        return {"ok": False, "error": "body must be a JSON object"}
    r = ctx.client("unifi").write(m, p, body)         # client WRITE_RULES enforce the allowlist
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "method": m, "path": p, "result": r}
