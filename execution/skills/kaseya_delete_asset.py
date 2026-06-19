"""Delete a Kaseya asset record — DESTRUCTIVE (D-69; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_delete_asset"
DESCRIPTION = ("DELETE one ASSET record from Kaseya (removes the asset-management record; the "
               "physical machine and its agent are unaffected). Pass the AssetId. Every run "
               "needs fresh owner approval (cannot be disabled).")
SOURCE = "kaseya"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "asset_id": {"type": "string", "description": "the AssetId to delete"},
    },
    "required": ["asset_id"],
    "additionalProperties": False,
}


def run(ctx, asset_id: str, **_: Any):
    asset_id = str(asset_id or "").strip()
    if not asset_id:
        return {"ok": False, "error": "an AssetId is required"}
    r = ctx.client("kaseya").write_destructive("DELETE",
                                               f"/assetmgmt/assets/{asset_id}")
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "deleted_asset": asset_id,
            "note": "the asset record was removed (the machine/agent itself is untouched)"}
