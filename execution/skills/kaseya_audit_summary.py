"""Kaseya audit summary snapshot for one machine (D-68; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_audit_summary"
DESCRIPTION = ("Show Kaseya's rolled-up AUDIT SUMMARY for ONE machine — the snapshot of key "
               "facts (system info, last audit time, OS, CPU/RAM, network) Kaseya collected at "
               "the last audit. Pass the machine name or AgentId.")
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
    data, e = k.result(client, f"/assetmgmt/audit/{aid}/summary")
    if e:
        return {"ok": False, "error": e}
    summary = data[0] if isinstance(data, list) and data else data
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid, "audit_summary": summary or {}}
