"""List Freshdesk ticket fields (incl. custom fields) (D-83)."""
from __future__ import annotations

from typing import Any

from . import _freshdesk_common as _f

NAME = "freshdesk_ticket_fields"
DESCRIPTION = ("List the Freshdesk ticket fields, including CUSTOM fields and their allowed "
               "values (dropdown choices) — so you know the exact field names/values to use when "
               "creating or updating tickets.")
SOURCE = "freshdesk"
GROUP = "freshdesk_admin"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
_FIELDS = ("id", "name", "label", "type", "default", "required_for_agents", "choices",
           "customers_can_edit")


def run(ctx, **_: Any):
    out = []
    for f in ctx.client("freshdesk").get_paginated("/ticket_fields"):
        out.append(_f.slim(f, _FIELDS))
    return out
