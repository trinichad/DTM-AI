"""kaseya_read — scoped generic read primitive for Kaseya VSA.

The AI can call ANY allow-listed Kaseya read endpoint (GET only) to compose learned
skills without new code. Path is enforced against the read allowlist in clients/scopes.py.
"""
from __future__ import annotations

from typing import Any

from execution.clients.scopes import READ_SCOPES, scoped_read

NAME = "kaseya_read"
DESCRIPTION = ("Read any allow-listed Kaseya VSA endpoint (GET). Use for Kaseya data not covered "
               f"by a specific tool. Allowed path prefixes: {', '.join(READ_SCOPES['kaseya'])}.")
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "params": {"type": "object"},
    },
    "required": ["path"],
    "additionalProperties": False,
}


def run(ctx, path: str, params: dict | None = None, **_: Any):
    return scoped_read(ctx, "kaseya", path, params)
