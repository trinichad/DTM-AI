"""user_memory_note — save a fact about the PERSON you are talking to (D-31).

Writes only to MSP AI's own vault (vault/users/<username>.md) — never to a client system.
The target user is the SIGNED-IN user bound to this conversation (from ctx), never a
model-chosen parameter, so the agent cannot write another person's profile.
"""
from __future__ import annotations

from typing import Any

from execution.core.memory import VaultStore

NAME = "user_memory_note"
DESCRIPTION = ("Save a NEW durable fact about the person you are talking to (their preferred email, "
               "phone, schedule, preferences). One fact per note. If the fact CONFLICTS with their "
               "saved profile, ask them first (update / keep / add both), then use "
               "user_memory_update to rewrite.")
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
    profile = (ctx._meta or {}).get("user_profile") or {}
    username = profile.get("username") or ""
    if not username:
        return {"error": "no signed-in user is bound to this conversation"}
    return VaultStore().append_user_memory(username, note, ctx.actor)
