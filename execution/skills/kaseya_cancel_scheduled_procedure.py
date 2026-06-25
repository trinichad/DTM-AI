"""Cancel a scheduled Kaseya agent procedure (D-69; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_cancel_scheduled_procedure"
DESCRIPTION = ("CANCEL a scheduled agent procedure on a machine (un-schedules a pending run; "
               "doesn't stop one already running). Give the procedure name (or id) and `machine` "
               "for one box, or `machines` (a list) to do MANY in ONE call — do NOT call this "
               "tool once per machine. See what's scheduled with kaseya_agent_procedures "
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
        "machines": {"type": "array", "items": {"type": "string"},
                     "description": "act on MANY machines in ONE call — a list of machine/agent "
                                    "names or AgentIds; results come back together. Use this "
                                    "instead of calling the tool once per machine."},
        "procedure": {"type": "string", "description": "scheduled procedure name (or id)"},
    },
    "required": ["procedure"],
    "additionalProperties": False,
}


def run(ctx, machine: str = "", machines: Any = None, procedure: str = "", **_: Any):
    wanted = [str(m).strip() for m in (machines or []) if str(m).strip()]
    if wanted:                                         # batch (D-110) — one call, many machines
        results = ctx.map_progress(wanted[:200], lambda m: _one(ctx, m, procedure))
        return {"ok": any(r.get("ok") for r in results), "machines_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, machine, procedure)


def _one(ctx, machine: str, procedure: str) -> dict:
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "machine": machine, "error": err}
    aid = agent.get("AgentId")
    pid, pname, perr = k.resolve_procedure(client, procedure)
    if perr:
        return {"ok": False, "machine": machine, "error": perr}
    r = client.write("DELETE", f"/automation/agentprocs/{aid}/{pid}")
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "machine": machine, "error": r["error"]}
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid, "cancelled_procedure": pname, "procedure_id": pid}
