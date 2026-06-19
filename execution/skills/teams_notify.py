"""Post an alert into the configured Microsoft Teams home conversation (D-29)."""
from __future__ import annotations

from typing import Any

NAME = "teams_notify"
DESCRIPTION = ("Post a notification message into the the MSP team's Microsoft Teams home "
               "conversation (TEAMS_HOME_CONVERSATION). Markdown supported.")
SOURCE = "msteams"
CATEGORY = "alert"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "message": {"type": "string", "description": "the notification text (markdown)"},
    },
    "required": ["message"],
    "additionalProperties": False,
}


def run(ctx, message: str, **_: Any):
    from execution.core import credentials
    env = credentials.require("msteams")
    home = (env.get("TEAMS_HOME_CONVERSATION") or "").strip()
    if not home:
        return {"error": "TEAMS_HOME_CONVERSATION is not set — add it on the Teams integration card"}
    return ctx.client("msteams").send_text(home, message or "")
