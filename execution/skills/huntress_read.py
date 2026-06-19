"""huntress_read — scoped generic read primitive for Huntress (GET, allow-listed paths)."""
from __future__ import annotations

from typing import Any

from execution.clients.scopes import READ_SCOPES, scoped_read

NAME = "huntress_read"
DESCRIPTION = ("Read any allow-listed Huntress endpoint (GET). Use for Huntress data not covered by a "
               f"specific tool. Allowed path prefixes: {', '.join(READ_SCOPES['huntress'])}.")
SOURCE = "huntress"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"path": {"type": "string"}, "params": {"type": "object"}},
    "required": ["path"],
    "additionalProperties": False,
}


def run(ctx, path: str, params: dict | None = None, **_: Any):
    return scoped_read(ctx, "huntress", path, params)
