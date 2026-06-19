"""Cylance global safe / quarantine list contents (D-82)."""
from __future__ import annotations

from typing import Any

NAME = "cylance_global_list"
DESCRIPTION = ("List the contents of a Cylance GLOBAL list for this client: `list`='safe' (files "
               "allowed everywhere) or 'quarantine' (files blocked everywhere). Returns each "
               "entry's sha256, name, category, reason, and who added it.")
SOURCE = "cylance"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
_LISTS = {"safe": 1, "quarantine": 0}        # Cylance listTypeId: 0=GlobalQuarantine, 1=GlobalSafe
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "list": {"type": "string", "enum": list(_LISTS), "description": "'safe' or 'quarantine'"},
    },
    "required": ["list"],
    "additionalProperties": False,
}
_FIELDS = ("sha256", "name", "category", "reason", "added", "added_by", "list_type")


def run(ctx, list: str, **_: Any):
    type_id = _LISTS.get((list or "").strip().lower())
    if type_id is None:
        return {"ok": False, "error": "list must be 'safe' or 'quarantine'"}
    out = []
    for e in ctx.client("cylance").get_paginated("/globallists/v2", {"listTypeId": type_id}):
        out.append({k: e.get(k) for k in _FIELDS if k in e} or e)
    return out
