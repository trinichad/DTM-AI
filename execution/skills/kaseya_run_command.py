"""Run a command on an endpoint through Kaseya (D-70; SOP: kaseya-vsa).

OWNER-AUTHORIZED command execution (extends Rule #6 by the owner's explicit direction). The agent
PROPOSES a command; the per-run approval gate means a HUMAN sees and approves the EXACT command
before it runs. Disabled by default; every run audited. Mechanism: schedules the owner's
"run command" Kaseya procedure to run now with the command as a prompt value (Kaseya REST can't
take a raw command — only run/schedule an existing procedure).
"""
from __future__ import annotations

from typing import Any

NAME = "kaseya_run_command"
DESCRIPTION = ("RUN a command on a machine through Kaseya (PowerShell). For troubleshooting and "
               "admin tasks — e.g. 'ipconfig /all', restart a service, or New-ADUser on a domain "
               "controller. The command runs on the endpoint and its output is captured; read it "
               "back with kaseya_command_output. Give the exact command and `machine` for one "
               "box, or `machines` (a list) to run it on MANY in ONE call — do NOT call this tool "
               "once per machine. HIGH RISK: you must approve the exact command before it runs. "
               "Confirm the command and target with the user before proposing it.")
SOURCE = "kaseya"
GROUP = "kaseya_command"      # clusters with the rest of the Command Toolkit in the UI (D-71)
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
        "command": {"type": "string",
                    "description": "the exact command to run (PowerShell by default)"},
        "power_up_if_offline": {"type": "boolean",
                                "description": "wake the machine if offline (default false)"},
    },
    "required": ["command"],
    "additionalProperties": False,
}


def run(ctx, machine: str = "", machines: Any = None, command: str = "",
        power_up_if_offline: bool = False, **_: Any):
    wanted = [str(m).strip() for m in (machines or []) if str(m).strip()]
    if wanted:                                         # batch (D-110) — one call, many machines
        results = [_one(ctx, m, command, power_up_if_offline) for m in wanted[:200]]
        return {"ok": any(r.get("ok") for r in results), "machines_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, machine, command, power_up_if_offline)


def _one(ctx, machine: str, command: str, power_up_if_offline: bool = False) -> dict:
    from . import _kaseya_common as k
    out = k.run_command(ctx, machine, command,
                        power_up_if_offline=bool(power_up_if_offline))
    out.setdefault("machine", machine)
    return out
