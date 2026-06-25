"""Flush the DNS resolver cache on a machine (D-71; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_flush_dns"
DESCRIPTION = ("Flush the DNS resolver cache on a machine (common fix for 'can't reach a site "
               "after a DNS change'). Pass `machine` for one box, or `machines` (a list) to do "
               "MANY in ONE call — do NOT call this tool once per machine. Runs `ipconfig "
               "/flushdns` through the Command Toolkit engine.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
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
    },
    "additionalProperties": False,
}


def run(ctx, machine: str = "", machines: Any = None, **_: Any):
    wanted = [str(m).strip() for m in (machines or []) if str(m).strip()]
    if wanted:                                         # batch (D-110) — one call, many machines
        results = ctx.map_progress(wanted[:200], lambda m: _one(ctx, m))
        return {"ok": any(r.get("ok") for r in results), "machines_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, machine)


def _one(ctx, machine: str) -> dict:
    from . import _kaseya_common as k
    out = k.run_command(ctx, machine, "ipconfig /flushdns")
    out.setdefault("machine", machine)
    if out.get("ok"):
        out["note"] = "DNS cache flush submitted — confirm with kaseya_command_output"
    return out
