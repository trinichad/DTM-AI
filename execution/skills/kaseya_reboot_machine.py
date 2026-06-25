"""Reboot a machine through Kaseya (D-71; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_reboot_machine"
DESCRIPTION = ("REBOOT a machine (forced restart). By default waits 60 seconds so a logged-in "
               "user gets a moment. HIGH RISK on a server or a machine someone's working on — "
               "confirm with the user first. Pass `machine` for one box, or `machines` (a list) "
               "to do MANY in ONE call — do NOT call this tool once per machine. Runs through the "
               "Command Toolkit engine.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
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
        "delay_seconds": {"type": "integer",
                          "description": "seconds before the reboot (default 60, 0 = immediate, "
                                         "max 3600)"},
    },
    "additionalProperties": False,
}


def run(ctx, machine: str = "", machines: Any = None, delay_seconds: int = 60, **_: Any):
    wanted = [str(m).strip() for m in (machines or []) if str(m).strip()]
    if wanted:                                         # batch (D-110) — one call, many machines
        results = ctx.map_progress(wanted[:200], lambda m: _one(ctx, m, delay_seconds))
        return {"ok": any(r.get("ok") for r in results), "machines_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, machine, delay_seconds)


def _one(ctx, machine: str, delay_seconds: int = 60) -> dict:
    from . import _kaseya_common as k
    try:
        delay = max(0, min(int(delay_seconds if delay_seconds is not None else 60), 3600))
    except (TypeError, ValueError):
        delay = 60
    command = f"shutdown /r /f /t {delay} /c \"Reboot scheduled by IT\""
    out = k.run_command(ctx, machine, command)
    out.setdefault("machine", machine)
    if out.get("ok"):
        out["rebooting_in_seconds"] = delay
        out["note"] = (f"reboot scheduled in {delay}s (cancel within that window with "
                       f"'shutdown /a' via kaseya_run_command if needed)")
    return out
