"""freshdesk_read — scoped generic read primitive for Freshdesk (GET, allow-listed paths)."""
from __future__ import annotations

from typing import Any

from execution.clients.scopes import READ_SCOPES, scoped_read

NAME = "freshdesk_read"
DESCRIPTION = ("Read any allow-listed Freshdesk endpoint (GET). Use for Freshdesk data not covered "
               f"by a specific tool. Allowed path prefixes: {', '.join(READ_SCOPES['freshdesk'])}.")
SOURCE = "freshdesk"
GROUP = "freshdesk_admin"
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
    return scoped_read(ctx, "freshdesk", path, params)
