"""One Kaseya machine's live health/detail record (D-68; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_machine_health"
DESCRIPTION = ("Show ONE machine's live Kaseya record: online/offline, last check-in, LAST "
               "REBOOT / uptime, OS + version, IP / gateway / DNS, RAM and CPU, last-logged-in "
               "user, and agent version. Pass the machine name or AgentId. Use this for 'is "
               "machine X up', 'when did it last reboot', 'what's its IP'.")
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
    },
    "required": ["machine"],
    "additionalProperties": False,
}

_FIELDS = ("AgentId", "AgentName", "ComputerName", "Online", "OSType", "OSInfo",
           "OperatingSystem", "OSVersion", "IPAddress", "IPv6Address", "DefaultGateway",
           "DNSServer1", "DNSServer2", "DHCPServer", "LastLoggedInUser", "CurrentUser",
           "LastRebootTime", "LastCheckInTime", "FirstCheckIn", "TimeZone", "Country",
           "RamMBytes", "CpuCount", "CpuSpeed", "CpuType", "DomainWorkgroup", "AgentVersion",
           "PrimaryKServer")


def run(ctx, machine: str, **_: Any):
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "error": err}
    aid = agent.get("AgentId")
    detail, err = k.result(client, f"/assetmgmt/agents/{aid}")
    if err:
        return {"ok": False, "error": err}
    row = detail if isinstance(detail, dict) else agent
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid, "health": k.slim(row, _FIELDS)}
