"""memory_note — save a durable note to this client's long-term memory.

CATEGORY=write, but SOURCE=msp_ai: this writes ONLY to MSP AI's own markdown vault, never to
a client system. Such internal writes are seeded allow_write=True in the Capability Console
(low-risk, reversible, audited, human-readable) — the owner can disable it there if desired.
"""
from __future__ import annotations

from typing import Any

from execution.core.memory import VaultStore

NAME = "memory_note"
DESCRIPTION = ("Add a NEW durable fact about this client to long-term memory (a recurring issue, an "
               "environment detail, a preference). One fact per note. To CHANGE or REMOVE an existing "
               "fact (something in the environment changed, or a fact was wrong), use memory_update.")
SOURCE = "msp_ai"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False  # internal vault write; not a client-system action
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"note": {"type": "string"}},
    "required": ["note"],
    "additionalProperties": False,
}


def run(ctx, note: str, **_: Any):
    return VaultStore().append_memory(ctx.tenant_id, note, ctx.actor)
