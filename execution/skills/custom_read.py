"""Generic read for owner-defined custom integrations (D-27) — scoped + read-only."""
from __future__ import annotations

from typing import Any

NAME = "custom_read"
DESCRIPTION = ("GET data from one of the owner's custom integrations (see custom_integrations "
               "for the list). Only paths inside that integration's read allowlist work.")
SOURCE = "msp_ai"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "integration": {"type": "string", "description": "the custom integration's id"},
        "path": {"type": "string", "description": "URL path beginning with '/', e.g. /v1/items"},
        "params": {"type": "object", "description": "optional query parameters"},
    },
    "required": ["integration", "path"],
    "additionalProperties": False,
}


def run(ctx, integration: str, path: str, params: dict | None = None, **_: Any):
    from execution.clients.scopes import scoped_read
    from execution.core.custom_integrations import get_store
    if get_store().get(integration) is None:
        return {"error": f"'{integration}' is not a custom integration "
                         "(built-ins have their own tools)"}
    return scoped_read(ctx, integration, path, params or None)
