"""user_memory_update — rewrite the profile of the person you are talking to (D-31).

For corrections: when a stored fact about the user is outdated or wrong AND the user has
confirmed the change (the prompt instructs the agent to ask update/keep/add first).
Bound to the signed-in user from ctx — never a model-chosen target. .bak kept on disk.
"""
from __future__ import annotations

from typing import Any

from execution.core.memory import VaultStore

NAME = "user_memory_update"
DESCRIPTION = ("REWRITE the saved profile of the person you are talking to with a corrected "
               "version — use only AFTER they confirmed the change (e.g. replacing a stored "
               "email). Pass the COMPLETE revised profile text; it overwrites the old one "
               "(a .bak backup is kept).")
SOURCE = "msp_ai"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False  # internal vault write; not a client-system action
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"content": {"type": "string",
                               "description": "the full revised profile (markdown)"}},
    "required": ["content"],
    "additionalProperties": False,
}


def run(ctx, content: str, **_: Any):
    profile = (ctx._meta or {}).get("user_profile") or {}
    username = profile.get("username") or ""
    if not username:
        return {"error": "no signed-in user is bound to this conversation"}
    return VaultStore().write_user_memory(username, content, ctx.actor)
