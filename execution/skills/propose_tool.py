"""Stage a new-tool draft for the owner's Build-tab review (D-40; SOP: self-development)."""
from __future__ import annotations

from typing import Any

NAME = "propose_tool"
DESCRIPTION = ("Draft a NEW tool as a sandboxed candidate when the user asks for a capability "
               "that doesn't exist yet (a missing read, or any change to client systems). FIRST "
               "tell the user what's missing and ASK whether they want the tool drafted; call "
               "this only after their explicit yes. The draft lands in the Build tab, where the "
               "owner reviews, tests and promotes it, then enables it in the Capability Console — "
               "it cannot run before that, and you cannot enable it. In `description` give "
               "precise requirements: goal, vendor/integration, exact API endpoints, parameters, "
               "category (read or write), expected output.")
SOURCE = "msp_ai"
CATEGORY = "write"            # writes only into the skills_candidate/ sandbox — never live code
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False     # the human gate IS the Build tab review → promote → enable chain
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string",
                        "description": "full requirements for the tool: what it does, which "
                                       "vendor/integration, exact API endpoints/paths, input "
                                       "parameters, category (read or write), expected output"},
    },
    "required": ["description"],
    "additionalProperties": False,
}


def run(ctx, description: str, **_: Any):
    if not (description or "").strip():
        return {"ok": False, "error": "describe the tool to draft"}
    from execution.core import builder
    # Draft with the SAME model this turn is running on (the one the user selected) — fall back to
    # a capable cloud model only if it's unknown (D-53).
    model_id = (getattr(ctx, "_meta", None) or {}).get("chat_model_id")
    r = builder.draft(description, model_id=model_id)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error") or "draft failed"}
    v = r.get("validation") or {}
    return {"ok": True, "candidate": r.get("name"),
            "drafted_with": f"{r.get('provider', '?')}/{r.get('model', '?')}",
            "validation_ok": bool(v.get("ok")),
            "issues": (v.get("issues") or [])[:8],
            "next": "Draft staged in the Build tab (sandbox — not live). The owner must review, "
                    "test and Promote it there, then enable it in the Capability Console. Until "
                    "then this capability still does not exist; do not claim otherwise."}
