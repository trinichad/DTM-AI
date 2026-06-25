"""Restart a Windows service on a machine (D-71; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_restart_service"
DESCRIPTION = ("Restart a Windows SERVICE on a machine (e.g. the print spooler, a line-of-"
               "business service). Pass the service name (the Windows service name, e.g. "
               "'Spooler', or its display name) and `machine` for one box, or `machines` (a list) "
               "to do MANY in ONE call — do NOT call this tool once per machine. Runs through the "
               "Command Toolkit engine; confirm with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
CATEGORY = "write"
RISK_LEVEL = "medium"
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
        "service": {"type": "string", "description": "the service name or display name"},
    },
    "required": ["service"],
    "additionalProperties": False,
}

_SVC = re.compile(r"^[A-Za-z0-9 ._()-]+$")            # service names — no shell metacharacters


def run(ctx, machine: str = "", machines: Any = None, service: str = "", **_: Any):
    wanted = [str(m).strip() for m in (machines or []) if str(m).strip()]
    if wanted:                                         # batch (D-110) — one call, many machines
        results = ctx.map_progress(wanted[:200], lambda m: _one(ctx, m, service))
        return {"ok": any(r.get("ok") for r in results), "machines_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, machine, service)


def _one(ctx, machine: str, service: str) -> dict:
    from . import _kaseya_common as k
    service = (service or "").strip()
    if not _SVC.match(service):
        return {"ok": False, "machine": machine,
                "error": f"'{service}' is not a valid service name"}
    esc = service.replace("'", "''")
    command = (f"Restart-Service -DisplayName '{esc}' -Force -ErrorAction SilentlyContinue; "
               f"Restart-Service -Name '{esc}' -Force -ErrorAction SilentlyContinue; "
               f"Get-Service -Name '{esc}','{esc}' -ErrorAction SilentlyContinue | "
               f"Select-Object Name,Status | Format-Table -AutoSize | Out-String")
    out = k.run_command(ctx, machine, command)
    out.setdefault("machine", machine)
    if out.get("ok"):
        out["restarting_service"] = service
        out["note"] = "service restart submitted — confirm status with kaseya_command_output"
    return out
