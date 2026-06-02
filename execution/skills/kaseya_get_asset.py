"""Get one Kaseya asset by AgentId/AgentGuid/AssetId (trimmed)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_get_asset"
DESCRIPTION = "Get details for one Kaseya asset by its AgentId (or AgentGuid/AssetId)."
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"asset_id": {"type": "string"}},
    "required": ["asset_id"],
    "additionalProperties": False,
}

_FIELDS = ("AgentId", "AgentGuid", "AssetId", "AssetName", "DisplayName", "OSType", "OSName",
           "IPAddresses", "LastSeenDate", "LastLoggedInUser", "CpuType", "TotalRamMBytes")


def run(ctx, asset_id: str, **_: Any):
    asset = ctx.client("kaseya").get_asset(asset_id)
    if not asset:
        return {"error": f"no Kaseya asset matched '{asset_id}'"}
    picked = {k: asset[k] for k in _FIELDS if k in asset}
    return picked or asset   # v2 field names may differ — return raw rather than all-null
