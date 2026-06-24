"""Schedule a Kaseya missing-patch scan on a machine (D-69; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_schedule_patch_scan"
DESCRIPTION = ("Schedule a missing-patch SCAN on a machine (refreshes Kaseya's view of what "
               "patches it needs). NOTE: this is a SCAN only — the Kaseya REST API cannot "
               "install/deploy patches (that requires running an agent procedure). Pass "
               "`machine` for one box, or `machines` (a list) to do MANY in ONE call — do NOT "
               "call this tool once per machine. By default runs as soon as possible. Low risk.")
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
        "power_up_if_offline": {"type": "boolean",
                                "description": "wake the machine if it's offline (default false)"},
    },
    "additionalProperties": False,
}


def run(ctx, machine: str = "", machines: Any = None, power_up_if_offline: bool = False, **_: Any):
    wanted = [str(m).strip() for m in (machines or []) if str(m).strip()]
    if wanted:                                         # batch (D-110) — one call, many machines
        results = [_one(ctx, m, power_up_if_offline) for m in wanted[:200]]
        return {"ok": any(r.get("ok") for r in results), "machines_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, machine, power_up_if_offline)


def _one(ctx, machine: str, power_up_if_offline: bool = False) -> dict:
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "machine": machine, "error": err}
    aid = agent.get("AgentId")
    # SCAN now via /assetmgmt/patch/{aid}/scannow — the patch-SCAN action. (NOT /patch/{aid}/schedule,
    # which schedules patch DEPLOYMENT — wrong + heavier than this tool's stated read-only intent.)
    body = {"SkipIfOffLine": False, "PowerUpIfOffLine": bool(power_up_if_offline)}
    r = client.write("PUT", f"/assetmgmt/patch/{aid}/scannow", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "machine": machine, "error": r["error"]}
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid,
            "note": "patch SCAN scheduled — read results with the audit tools after it runs; "
                    "installing patches needs an agent procedure (kaseya_run_procedure)"}
