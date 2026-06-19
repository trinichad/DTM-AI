"""List Freshdesk agents (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_list_agents"
DESCRIPTION = ("List the Freshdesk agents (your support staff) — id, name, email, and active "
               "state. Use this to find an agent_id to assign tickets to.")
SOURCE = "freshdesk"
GROUP = "freshdesk_team"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


def run(ctx, **_: Any):
    out = []
    for a in ctx.client("freshdesk").get_paginated("/agents"):
        contact = a.get("contact") or {}
        out.append({"id": a.get("id"), "name": contact.get("name"), "email": contact.get("email"),
                    "active": contact.get("active"), "occasional": a.get("occasional"),
                    "type": a.get("type")})
    return out
