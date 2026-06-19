"""Custom fields tracked on a Kaseya asset (D-68; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_asset_custom_fields"
DESCRIPTION = ("Show the CUSTOM FIELDS your team tracks on a machine/asset in Kaseya (e.g. "
               "warranty, location, owner — whatever custom fields are configured). Pass the "
               "machine name or AgentId.")
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
    },
    "required": ["machine"],
    "additionalProperties": False,
}


def run(ctx, machine: str, **_: Any):
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "error": err}
    aid = agent.get("AgentId")
    data, e = k.result(client, f"/assetmgmt/assets/{aid}/customfields")
    if e:
        return {"ok": False, "error": e}
    fields = {}
    for r in k.rows(data):
        name = r.get("FieldName") or r.get("Name") or r.get("Title")
        val = r.get("FieldValue") if "FieldValue" in r else r.get("Value")
        if name is not None:
            fields[str(name)] = val
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid,
            "custom_fields": fields or k.rows(data),   # raw rows if the shape differs
            **({"note": "no custom fields set on this asset"} if not k.rows(data) else {})}
