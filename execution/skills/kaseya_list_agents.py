"""List managed Kaseya VSA AGENTS (the machine-group view; trimmed payload)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_list_agents"
DESCRIPTION = (
    "List managed AGENTS (machines that have the Kaseya agent installed) in Kaseya VSA — the "
    "authoritative machine-group view, i.e. exactly what you see under a machine group in the Kaseya "
    "console. Use THIS (not kaseya_list_assets) for questions like 'which agents/machines are in "
    "group X' or 'does machine Y exist', because a machine can be a managed agent without an "
    "asset-management record. Optional name_contains does a case-insensitive substring filter on the "
    "machine/group name (e.g. 'acme') so you get a complete, focused result."
)
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name_contains": {"type": "string",
                          "description": "case-insensitive substring filter on the machine/agent name or machine group"}
    },
    "additionalProperties": False,
}

# Candidate fields across VSA versions; we keep whatever is present and fall back to the raw row so
# nothing is ever silently nulled away.
_FIELDS = ("AgentId", "AgentGuid", "AgentName", "ComputerName", "DisplayName", "AssetName",
           "MachineGroup", "MachineGroupId", "GroupName", "OrgName", "OSName", "OSType",
           "IPAddress", "IPAddresses", "Online", "LastCheckinTime", "LastSeenDate")
_HAY = ("AgentName", "ComputerName", "DisplayName", "AssetName", "MachineGroup", "GroupName", "OrgName")


def run(ctx, name_contains: str = "", **_: Any):
    rows = ctx.client("kaseya").get_agents()
    needle = (name_contains or "").strip().lower()
    if needle:
        rows = [r for r in rows
                if needle in " ".join(str(r.get(k, "")) for k in _HAY).lower()]
    out = []
    for r in rows:
        picked = {k: r[k] for k in _FIELDS if k in r}
        out.append(picked or r)   # never null everything out — pass the raw row if fields differ
    return out
