"""List the owner's custom integrations + their readable paths (D-27) — metadata only."""
from __future__ import annotations

from typing import Any

NAME = "custom_integrations"
DESCRIPTION = ("List the owner-defined custom integrations: id, label, base URL and which "
               "path prefixes custom_read may GET. Never returns credentials.")
SOURCE = "msp_ai"
CATEGORY = "read"
RISK_LEVEL = "none"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def run(ctx, **_: Any):
    from execution.core import credentials
    from execution.core.custom_integrations import get_store
    out = []
    for ci in get_store().all():
        out.append({"id": ci.id, "label": ci.label, "base_url": ci.base_url,
                    "read_paths": ci.read_paths,
                    "configured": credentials.is_configured(ci.id),
                    "notes": ci.notes})
    return out or {"info": "no custom integrations defined yet"}
