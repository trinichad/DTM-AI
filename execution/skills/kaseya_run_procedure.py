"""Run a Kaseya agent procedure now on a machine (D-69; SOP: kaseya-vsa).

HIGHEST-RISK write: an agent procedure is human-authored automation that runs ON the endpoint —
effectively code execution on the machine. It is still a PRE-DEFINED procedure selected by
name/id (never a free-form command), so Rule #6 holds; but it is approval-gated and disabled by
default for good reason.
"""
from __future__ import annotations

from typing import Any

NAME = "kaseya_run_procedure"
DESCRIPTION = ("RUN an existing Kaseya AGENT PROCEDURE immediately on a machine. An agent "
               "procedure is automation your team authored in Kaseya (e.g. clear temp files, "
               "restart a service) — running it executes that automation ON the endpoint. Give "
               "the procedure name (or id) and `machine` for one box, or `machines` (a list) to "
               "run it on MANY in ONE call — do NOT call this tool once per machine. HIGH RISK: "
               "only runs a procedure that already exists in Kaseya; confirm the exact procedure "
               "with the user first.")
SOURCE = "kaseya"
CATEGORY = "write"
RISK_LEVEL = "high"
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
        "procedure": {"type": "string", "description": "agent-procedure name (or id) to run"},
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
    r = client.write("PUT", f"/automation/agentprocs/{aid}/{pid}/runnow")
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "machine": machine, "error": r["error"]}
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid, "procedure": pname, "procedure_id": pid,
            "note": "the procedure was submitted to run on the machine — confirm the outcome "
                    "with kaseya_agent_procedures (history) after it runs"}
