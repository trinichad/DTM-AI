"""Run a Kaseya audit now on a machine (D-69; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_run_audit"
DESCRIPTION = ("Trigger a Kaseya AUDIT to run now on one machine so its inventory is refreshed. "
               "type: 'latest' (default — quick inventory refresh), 'baseline' (full baseline), "
               "or 'sysinfo' (DMI/SMBIOS hardware). Low risk: collects data, changes nothing on "
               "the box. Useful before reading software/hardware so the data is current.")
SOURCE = "kaseya"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_TYPES = ("latest", "baseline", "sysinfo")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
        "type": {"type": "string", "enum": list(_TYPES),
                 "description": "audit type (default latest)"},
    },
    "required": ["machine"],
    "additionalProperties": False,
}


def run(ctx, machine: str, type: str = "latest", **_: Any):
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    atype = (type or "latest").strip().lower()
    if atype not in _TYPES:
        return {"ok": False, "error": f"type must be one of: {', '.join(_TYPES)}"}
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "error": err}
    aid = agent.get("AgentId")
    r = client.write("PUT", f"/assetmgmt/audit/{atype}/{aid}/runnow")
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid, "audit_type": atype,
            "note": "audit submitted — results land after the agent checks in and runs it"}
