"""Add a hash to a Cylance global safe/quarantine list (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_globallist_add"
DESCRIPTION = ("Add a file hash to a Cylance GLOBAL list for the whole tenant: `list`='safe' "
               "(allow everywhere) or 'quarantine' (block everywhere). Give the `sha256` and a "
               "`reason`. For 'safe' you may set a `category`. Remove later with "
               "cylance_globallist_remove.")
SOURCE = "cylance"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_LISTS = {"safe": "GlobalSafe", "quarantine": "GlobalQuarantine"}
_CATEGORIES = ("Admin Tool", "Commercial Software", "Drivers", "Internal Application",
               "Operating System", "Security Software", "None")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "list": {"type": "string", "enum": list(_LISTS), "description": "'safe' or 'quarantine'"},
        "sha256": {"type": "string", "description": "the file's SHA-256 hash"},
        "reason": {"type": "string", "description": "why it's being listed"},
        "category": {"type": "string", "enum": list(_CATEGORIES),
                     "description": "safe-list category (default None)"},
    },
    "required": ["list", "sha256", "reason"],
    "additionalProperties": False,
}


def run(ctx, list: str, sha256: str, reason: str, category: str = "None", **_: Any):
    list_type = _LISTS.get((list or "").strip().lower())
    h = (sha256 or "").strip().lower()
    rsn = (reason or "").strip()
    if not list_type:
        return {"ok": False, "error": "list must be 'safe' or 'quarantine'"}
    if not re.match(r"^[0-9a-f]{64}$", h):
        return {"ok": False, "error": "sha256 must be a 64-character hex hash"}
    if not rsn:
        return {"ok": False, "error": "give a reason"}
    body = {"sha256": h, "list_type": list_type, "reason": rsn[:500]}
    if list_type == "GlobalSafe":
        cat = category if category in _CATEGORIES else "None"
        body["category"] = cat
    r = ctx.client("cylance").write("POST", "/globallists/v2", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "list": list_type, "sha256": h, "note": "hash added to the global list"}
