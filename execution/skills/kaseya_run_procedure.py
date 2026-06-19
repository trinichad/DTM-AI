"""Run a Kaseya agent procedure now on a machine (D-69; SOP: kaseya-vsa).

HIGHEST-RISK write: an agent procedure is human-authored automation that runs ON the endpoint —
effectively code execution on the machine. It is still a PRE-DEFINED procedure selected by
name/id (never a free-form command), so Rule #6 holds; but it is approval-gated and disabled by
default for good reason.
"""
from __future__ import annotations

from typing import Any

NAME = "kaseya_run_procedure"
DESCRIPTION = ("RUN an existing Kaseya AGENT PROCEDURE immediately on one machine. An agent "
               "procedure is automation your team authored in Kaseya (e.g. clear temp files, "
               "restart a service) — running it executes that automation ON the endpoint. Give "
               "the machine name/AgentId and the procedure name (or id). HIGH RISK: only runs a "
               "procedure that already exists in Kaseya; confirm the exact procedure with the "
               "user first.")
SOURCE = "kaseya"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
        "procedure": {"type": "string", "description": "agent-procedure name (or id) to run"},
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
    r = client.write("PUT", f"/automation/agentprocs/{aid}/{pid}/runnow")
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid, "procedure": pname, "procedure_id": pid,
            "note": "the procedure was submitted to run on the machine — confirm the outcome "
                    "with kaseya_agent_procedures (history) after it runs"}
