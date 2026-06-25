"""Agent-procedure history / schedule for a Kaseya machine (D-68; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_agent_procedures"
DESCRIPTION = ("Show a machine's Kaseya AGENT PROCEDURES: view='history' (default) = which "
               "scripts/automations RAN and whether they succeeded, with timestamps; "
               "view='scheduled' = what's scheduled to run. Pass `machine` for one box, or "
               "`machines` (a list) to do MANY in ONE call — do NOT call this tool once per "
               "machine. Use for 'did the maintenance script run on X', 'what's scheduled on X'.")
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
        "machines": {"type": "array", "items": {"type": "string"},
                     "description": "act on MANY machines in ONE call — a list of machine/agent "
                                    "names or AgentIds; results come back together. Use this "
                                    "instead of calling the tool once per machine."},
        "view": {"type": "string", "enum": ["history", "scheduled"],
                 "description": "run history (default) or the schedule"},
    },
    "additionalProperties": False,
}

_HIST = ("ScriptName", "AgentProcedureName", "ProcedureName", "LastExecutionTime",
         "ExecutionTime", "Status", "Result", "Admin", "User")
_SCHED = ("ScriptName", "AgentProcedureName", "ProcedureName", "ScheduledTime", "NextRunTime",
          "Recurrence", "Admin")


def run(ctx, machine: str = "", machines: Any = None, view: str = "history", **_: Any):
    wanted = [str(m).strip() for m in (machines or []) if str(m).strip()]
    if wanted:                                         # batch (D-110) — one call, many machines
        results = ctx.map_progress(wanted[:200], lambda m: _one(ctx, m, view))
        return {"ok": any(r.get("ok") for r in results), "machines_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, machine, view)


def _one(ctx, machine: str, view: str = "history") -> dict:
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "machine": machine, "error": err}
    aid = agent.get("AgentId")
    view = (view or "history").strip().lower()
    # VSA 9 endpoints: .../{aid}/history and .../{aid}/scheduledprocs (NOT /scheduled, which 404s).
    seg, fields, key = (("history", _HIST, "procedure_history") if view != "scheduled"
                        else ("scheduledprocs", _SCHED, "scheduled_procedures"))
    data, e = k.result(client, f"/automation/agentprocs/{aid}/{seg}")
    if e:
        return {"ok": False, "machine": machine, "error": e}
    rows = [k.slim(r, fields) for r in k.rows(data)]
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid, "count": len(rows), key: rows}
