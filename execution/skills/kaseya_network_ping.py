"""Ping a host FROM a machine, for network troubleshooting (D-71; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_network_ping"
DESCRIPTION = ("Run a network PING from a machine to a target host/IP (to troubleshoot "
               "connectivity on a client's network — e.g. ping the gateway, a server, or a "
               "device). Pass `machine` for one box, or `machines` (a list) to ping FROM MANY in "
               "ONE call — do NOT call this tool once per machine. Runs through the Command "
               "Toolkit engine; read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
CATEGORY = "write"           # runs a command on the endpoint (gated like all command tools)
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent to ping FROM (name/AgentId)"},
        "machines": {"type": "array", "items": {"type": "string"},
                     "description": "act on MANY machines in ONE call — a list of machine/agent "
                                    "names or AgentIds; results come back together. Use this "
                                    "instead of calling the tool once per machine."},
        "target": {"type": "string", "description": "host name or IP to ping"},
        "count": {"type": "integer", "description": "number of pings (default 4, max 20)"},
    },
    "required": ["target"],
    "additionalProperties": False,
}

_TARGET = re.compile(r"^[A-Za-z0-9._:-]+$")           # hostname or IP — no shell metacharacters


def run(ctx, machine: str = "", machines: Any = None, target: str = "", count: int = 4, **_: Any):
    wanted = [str(m).strip() for m in (machines or []) if str(m).strip()]
    if wanted:                                         # batch (D-110) — one call, many machines
        results = ctx.map_progress(wanted[:200], lambda m: _one(ctx, m, target, count))
        return {"ok": any(r.get("ok") for r in results), "machines_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, machine, target, count)


def _one(ctx, machine: str, target: str, count: int = 4) -> dict:
    from . import _kaseya_common as k
    target = (target or "").strip()
    if not _TARGET.match(target):
        return {"ok": False, "machine": machine, "error": f"'{target}' is not a valid host/IP"}
    try:
        n = max(1, min(int(count or 4), 20))
    except (TypeError, ValueError):
        n = 4
    command = f"Test-Connection -ComputerName {target} -Count {n} | Format-Table -AutoSize | Out-String"
    out = k.run_command(ctx, machine, command)
    out.setdefault("machine", machine)
    if out.get("ok"):
        out["pinging"] = target
        out["note"] = f"pinging {target} ({n}x) from the machine — read the result with " \
                      f"kaseya_command_output"
    return out
