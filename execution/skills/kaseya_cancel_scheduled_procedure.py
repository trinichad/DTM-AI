"""Cancel a scheduled Kaseya agent procedure (D-69; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_cancel_scheduled_procedure"
DESCRIPTION = ("CANCEL a scheduled agent procedure on one machine (un-schedules a pending run; "
               "doesn't stop one already running). Give the machine name/AgentId and the "
               "procedure name (or id). See what's scheduled with kaseya_agent_procedures "
               "view=scheduled.")
SOURCE = "kaseya"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
        "procedure": {"type": "string", "description": "scheduled procedure name (or id)"},
    },
    "required": ["machine", "procedure"],
    "additionalProperties": False,
}


def run(ctx, machine: str, procedure: str, **_: Any):
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "error": err}
    aid = agent.get("AgentId")
    pid, pname, perr = k.resolve_procedure(client, procedure)
    if perr:
        return {"ok": False, "error": perr}
    r = client.write("DELETE", f"/automation/agentprocs/{aid}/{pid}")
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid, "cancelled_procedure": pname, "procedure_id": pid}
