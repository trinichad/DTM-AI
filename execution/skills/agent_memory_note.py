"""agent_memory_note — the running agent saves a durable lesson to its OWN long-term memory.

CATEGORY=write but SOURCE=dtm_ai: writes only to the agent's MEMORY.md inside its profile dir,
never to a client system (same posture as memory_note, which is for per-CLIENT facts). The agent
loop injects the active profile via ctx._meta["profile"]; without one the note lands on the
manager (default). Exact duplicates are skipped.
"""
from __future__ import annotations

from typing import Any

from execution.core.agents import append_agent_memory

NAME = "agent_memory_note"
DESCRIPTION = ("Save a durable LESSON ABOUT YOUR OWN WORK to your long-term memory (a procedure "
               "that worked, a pitfall to avoid, a preference the owner expressed). One concise "
               "fact per note. For facts about a CLIENT's environment use memory_note instead.")
SOURCE = "dtm_ai"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False  # internal profile-dir write; not a client-system action
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"fact": {"type": "string"}},
    "required": ["fact"],
    "additionalProperties": False,
}


def run(ctx, fact: str, **_: Any):
    profile = (getattr(ctx, "_meta", None) or {}).get("profile") or "default"
    return append_agent_memory(profile, fact)
