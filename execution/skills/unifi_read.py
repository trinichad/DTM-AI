"""unifi_read — scoped generic read primitive for the UniFi Network API (GET, allow-listed)."""
from __future__ import annotations

from typing import Any

from execution.clients.scopes import READ_SCOPES, scoped_read

NAME = "unifi_read"
DESCRIPTION = ("Read any allow-listed UniFi Network endpoint (GET) not covered by a specific tool "
               "— e.g. /v1/sites/{id}/firewall/policies, /dns/policies, /acl-rules, /wans, "
               "/vpn/servers, /switching/lags, /info. Allowed prefixes: "
               f"{', '.join(READ_SCOPES['unifi'])} (and everything under /v1/sites/...).")
SOURCE = "unifi"
GROUP = "unifi"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"path": {"type": "string"}, "params": {"type": "object"}},
    "required": ["path"],
    "additionalProperties": False,
}


def run(ctx, path: str, params: dict | None = None, **_: Any):
    return scoped_read(ctx, "unifi", path, params)
