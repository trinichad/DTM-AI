"""List managed Kaseya VSA assets (trimmed payload)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_list_assets"
DESCRIPTION = ("List all managed assets/agents in Kaseya VSA for this client. "
               "Returns AgentId, AssetName, OSType, OSName, IPAddresses, LastSeenDate.")
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}

_FIELDS = ("AgentId", "AssetName", "DisplayName", "OSType", "OSName", "IPAddresses", "LastSeenDate")


def run(ctx, **_: Any):
    assets = ctx.client("kaseya").get_assets()
    return [{k: a.get(k) for k in _FIELDS} for a in assets]
