"""echo_note — a trivial read tool with a real PARAMETERS schema.

Used to demonstrate (and test) JSON-Schema arg validation: it requires `note` and
constrains `level` to an enum. Harmless; safe to leave enabled.
"""
from __future__ import annotations

from typing import Any

NAME = "echo_note"
DESCRIPTION = "Echo a short note back, tagged with a severity level. Useful for testing."
SOURCE = "dtm_ai"
CATEGORY = "read"
RISK_LEVEL = "none"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "note": {"type": "string"},
        "level": {"type": "string", "enum": ["info", "warning", "critical"]},
    },
    "required": ["note"],
    "additionalProperties": False,
}


def run(ctx, note: str, level: str = "info", **_: Any) -> dict[str, Any]:
    return {"tenant_id": ctx.tenant_id, "level": level, "note": note}
