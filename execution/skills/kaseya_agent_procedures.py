"""Agent-procedure history / schedule for a Kaseya machine (D-68; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_agent_procedures"
DESCRIPTION = ("Show a machine's Kaseya AGENT PROCEDURES: view='history' (default) = which "
               "scripts/automations RAN and whether they succeeded, with timestamps; "
               "view='scheduled' = what's scheduled to run. Pass the machine name or AgentId. "
               "Use for 'did the maintenance script run on X', 'what's scheduled on X'.")
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
        "view": {"type": "string", "enum": ["history", "scheduled"],
                 "description": "run history (default) or the schedule"},
    },
    "required": ["machine"],
    "additionalProperties": False,
}

_HIST = ("ScriptName", "AgentProcedureName", "ProcedureName", "LastExecutionTime",
         "ExecutionTime", "Status", "Result", "Admin", "User")
_SCHED = ("ScriptName", "AgentProcedureName", "ProcedureName", "ScheduledTime", "NextRunTime",
          "Recurrence", "Admin")


def run(ctx, machine: str, view: str = "history", **_: Any):
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "error": err}
    aid = agent.get("AgentId")
    view = (view or "history").strip().lower()
    # VSA 9 endpoints: .../{aid}/history and .../{aid}/scheduledprocs (NOT /scheduled, which 404s).
    seg, fields, key = (("history", _HIST, "procedure_history") if view != "scheduled"
                        else ("scheduledprocs", _SCHED, "scheduled_procedures"))
    data, e = k.result(client, f"/automation/agentprocs/{aid}/{seg}")
    if e:
        return {"ok": False, "error": e}
    rows = [k.slim(r, fields) for r in k.rows(data)]
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid, "count": len(rows), key: rows}
