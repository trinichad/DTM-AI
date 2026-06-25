"""Kaseya audit summary snapshot for one machine (D-68; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_audit_summary"
DESCRIPTION = ("Show Kaseya's rolled-up AUDIT SUMMARY for a machine — the snapshot of key "
               "facts (system info, last audit time, OS, CPU/RAM, network) Kaseya collected at "
               "the last audit. Pass `machine` for one box, or `machines` (a list) to do MANY in "
               "ONE call — do NOT call this tool once per machine.")
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
    client = ctx.client("kaseya")
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "machine": machine, "error": err}
    aid = agent.get("AgentId")
    data, e = k.result(client, f"/assetmgmt/audit/{aid}/summary")
    if e:
        return {"ok": False, "machine": machine, "error": e}
    summary = data[0] if isinstance(data, list) and data else data
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid, "audit_summary": summary or {}}
