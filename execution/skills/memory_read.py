"""memory_read — read the agent's long-term memory notes for the current client."""
from __future__ import annotations

from typing import Any

from execution.core.memory import VaultStore

NAME = "memory_read"
DESCRIPTION = ("Read MSP AI's saved long-term memory/notes about this specific client "
               "(past issues, preferences, things learned). Call early to recall context.")
SOURCE = "msp_ai"
CATEGORY = "read"
RISK_LEVEL = "none"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


def run(ctx, **_: Any):
    text = VaultStore().read_memory(ctx.tenant_id)
    return {"tenant_id": ctx.tenant_id, "memory": text, "has_memory": bool(text.strip())}
