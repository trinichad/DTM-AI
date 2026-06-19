"""Read back the output of a Kaseya run-command (D-70; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_command_output"
DESCRIPTION = ("Read back the OUTPUT of the last command run on a machine via kaseya_run_command "
               "(the procedure writes its output to a Kaseya custom field, which this reads). "
               "Pass the machine name/AgentId. If empty, the command may still be running.")
SOURCE = "kaseya"
GROUP = "kaseya_command"      # part of the Command Toolkit family (D-71)
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
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
    from execution.core.config import get_config
    from . import _kaseya_common as k
    cfg = get_config()
    field = (cfg.get("KASEYA_COMMAND_OUTPUT_FIELD") or "AI_Command_Output").strip()
    client = ctx.client("kaseya")
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "error": err}
    aid = agent.get("AgentId")
    data, e = k.result(client, f"/assetmgmt/assets/{aid}/customfields")
    if e:
        return {"ok": False, "error": e}
    val = None
    for row in k.rows(data):
        name = row.get("FieldName") or row.get("Name") or row.get("Title")
        if str(name or "").lower() == field.lower():
            val = row.get("FieldValue") if "FieldValue" in row else row.get("Value")
            break
    machine_name = agent.get("AgentName") or agent.get("ComputerName")
    if val in (None, ""):
        return {"ok": True, "machine": machine_name, "command_output": None,
                "note": f"no output in the '{field}' field yet — the command may still be "
                        f"running, or the procedure isn't writing output to that field"}
    return {"ok": True, "machine": machine_name, "command_output": val}
