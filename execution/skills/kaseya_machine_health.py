"""One Kaseya machine's live health/detail record (D-68; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_machine_health"
DESCRIPTION = ("Show ONE machine's live Kaseya record: online/offline, last check-in, LAST "
               "REBOOT / uptime, OS + version, IP / gateway / DNS, RAM and CPU, last-logged-in "
               "user, and agent version. Pass `machine` for one box, or `machines` (a list) to "
               "do MANY in ONE call — do NOT call this tool once per machine. Use this for 'is "
               "machine X up', 'when did it last reboot', 'what's its IP'.")
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

_FIELDS = ("AgentId", "AgentName", "ComputerName", "Online", "OSType", "OSInfo",
           "OperatingSystem", "OSVersion", "IPAddress", "IPv6Address", "DefaultGateway",
           "DNSServer1", "DNSServer2", "DHCPServer", "LastLoggedInUser", "CurrentUser",
           "LastRebootTime", "LastCheckInTime", "FirstCheckIn", "TimeZone", "Country",
           "RamMBytes", "CpuCount", "CpuSpeed", "CpuType", "DomainWorkgroup", "AgentVersion",
           "PrimaryKServer")


def run(ctx, machine: str = "", machines: Any = None, **_: Any):
    wanted = [str(m).strip() for m in (machines or []) if str(m).strip()]
    if wanted:                                         # batch (D-110) — one call, many machines
        results = [_one(ctx, m) for m in wanted[:200]]
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
    detail, err = k.result(client, f"/assetmgmt/agents/{aid}")
    if err:
        return {"ok": False, "machine": machine, "error": err}
    row = detail if isinstance(detail, dict) else agent
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid, "health": k.slim(row, _FIELDS)}
