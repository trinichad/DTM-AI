"""memory_note — save a durable note to this client's long-term memory.

CATEGORY=write, but SOURCE=dtm_ai: this writes ONLY to DTM AI's own markdown vault, never to
a client system. Such internal writes are seeded allow_write=True in the Capability Console
(low-risk, reversible, audited, human-readable) — the owner can disable it there if desired.
"""
from __future__ import annotations

from typing import Any

from execution.core.memory import VaultStore

NAME = "memory_note"
DESCRIPTION = ("Save a short, durable note about this client to long-term memory "
               "(e.g. a recurring issue, a preference, something learned). One fact per note.")
SOURCE = "dtm_ai"
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
