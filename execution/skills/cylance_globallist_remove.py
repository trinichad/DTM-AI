"""Remove a hash from a Cylance global safe/quarantine list (D-82). Opposite of globallist_add."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_globallist_remove"
DESCRIPTION = ("Remove a file hash from a Cylance GLOBAL list: `list`='safe' or 'quarantine'. Give "
               "the `sha256`.")
SOURCE = "cylance"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_LISTS = {"safe": "GlobalSafe", "quarantine": "GlobalQuarantine"}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "list": {"type": "string", "enum": list(_LISTS), "description": "'safe' or 'quarantine'"},
        "sha256": {"type": "string", "description": "the file's SHA-256 hash"},
    },
    "required": ["list", "sha256"],
    "additionalProperties": False,
}


def run(ctx, list: str, sha256: str, **_: Any):
    list_type = _LISTS.get((list or "").strip().lower())
    h = (sha256 or "").strip().lower()
    if not list_type:
        return {"ok": False, "error": "list must be 'safe' or 'quarantine'"}
    if not re.match(r"^[0-9a-f]{64}$", h):
        return {"ok": False, "error": "sha256 must be a 64-character hex hash"}
    r = ctx.client("cylance").write("DELETE", "/globallists/v2",
                                    {"sha256": h, "list_type": list_type})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "list": list_type, "sha256": h, "note": "hash removed from the global list"}
