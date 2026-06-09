"""memory_update — replace a client's long-term memory with a corrected/updated version.

Memory is a LIVING document of the client's current environment, not an append-only log. When
something changes (a firewall upgraded, computers swapped, a contact leaves, a fact was wrong),
the agent reads memory, revises it, and writes the FULL updated version back. The prior version
is kept as a one-step backup.

CATEGORY=write, but SOURCE=dtm_ai: this writes ONLY to DTM AI's own markdown vault, never to a
client system. Such internal writes are seeded allow_write=True in the Capability Console
(low-risk, reversible, audited, human-readable) — the owner can disable it there if desired.
"""
from __future__ import annotations

from typing import Any

from execution.core.memory import VaultStore

NAME = "memory_update"
DESCRIPTION = ("Replace this client's long-term memory with a corrected/updated version. Use this to "
               "CHANGE or REMOVE facts when the environment changes (firewall upgraded, computers "
               "swapped, a contact left) or a fact was wrong. Call memory_read first, revise the "
               "text, then pass the FULL updated memory as `content` (it overwrites). To merely ADD "
               "a new fact, use memory_note instead.")
SOURCE = "dtm_ai"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False  # internal vault write; not a client-system action
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"content": {"type": "string"}},
    "required": ["content"],
    "additionalProperties": False,
}


def run(ctx, content: str, **_: Any):
    return VaultStore().write_memory(ctx.tenant_id, content, ctx.actor)
