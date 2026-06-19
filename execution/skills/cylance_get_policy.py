"""Cylance policy detail — full settings of one policy (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "cylance_get_policy"
DESCRIPTION = ("Get one Cylance policy's full settings by `policy_id` — protection options, "
               "memory protection, script control, exclusions, etc.")
SOURCE = "cylance"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "policy_id": {"type": "string", "description": "the Cylance policy id (GUID)"},
    },
    "required": ["policy_id"],
    "additionalProperties": False,
}


def run(ctx, policy_id: str, **_: Any):
    pid = (policy_id or "").strip()
    if not re.match(r"^[A-Za-z0-9-]+$", pid):
        return {"ok": False, "error": "policy_id is not valid"}
    return ctx.client("cylance").get(f"/policies/v2/{pid}")
