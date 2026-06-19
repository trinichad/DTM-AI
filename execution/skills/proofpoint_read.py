"""proofpoint_read — scoped generic read primitive for Proofpoint Essentials (GET, allow-listed)."""
from __future__ import annotations

from typing import Any

from execution.clients.scopes import READ_SCOPES, scoped_read

NAME = "proofpoint_read"
DESCRIPTION = ("Read any allow-listed Proofpoint Essentials endpoint (GET) not covered by a "
               "specific tool — e.g. /endpoints/{domain} (which stack an org is on), or other "
               f"/orgs/... resources. Allowed prefixes: {', '.join(READ_SCOPES['proofpoint'])}.")
SOURCE = "proofpoint"
GROUP = "proofpoint"
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
    return scoped_read(ctx, "proofpoint", path, params)
