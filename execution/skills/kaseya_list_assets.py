"""List managed Kaseya VSA assets (trimmed payload)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_list_assets"
DESCRIPTION = ("List managed Kaseya VSA asset-management records for this client. "
               "Returns AgentId, AssetName, OSType, OSName, IPAddresses, LastSeenDate. "
               "For 'which agents/machines are in group X' prefer kaseya_list_agents. "
               "Optional name_contains does a case-insensitive substring filter (e.g. 'acme') so "
               "large fleets return a complete focused result instead of a truncated one.")
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name_contains": {"type": "string",
                          "description": "case-insensitive substring filter on the asset/machine name or group"}
    },
    "additionalProperties": False,
}

_FIELDS = ("AgentId", "AssetName", "DisplayName", "OSType", "OSName", "IPAddresses", "LastSeenDate")
_HAY = ("AssetName", "DisplayName", "ComputerName")


def run(ctx, name_contains: str = "", **_: Any):
    assets = ctx.client("kaseya").get_assets()
    needle = (name_contains or "").strip().lower()
    if needle:
        assets = [a for a in assets
                  if needle in " ".join(str(a.get(k, "")) for k in _HAY).lower()]
    out = []
    for a in assets:
        picked = {k: a[k] for k in _FIELDS if k in a}
        out.append(picked or a)   # v2 field names may differ — pass the row raw rather than all-null
    return out
