"""cylance_read — scoped generic read primitive for Cylance (GET, allow-listed paths)."""
from __future__ import annotations

from typing import Any

from execution.clients.scopes import READ_SCOPES, scoped_read

NAME = "cylance_read"
DESCRIPTION = ("Read any allow-listed Cylance endpoint (GET). Use for Cylance data not covered by a "
               f"specific tool. Allowed path prefixes: {', '.join(READ_SCOPES['cylance'])}.")
SOURCE = "cylance"
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
    return scoped_read(ctx, "cylance", path, params)
