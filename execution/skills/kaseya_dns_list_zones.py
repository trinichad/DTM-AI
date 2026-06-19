"""List DNS zones on a Windows DNS server (D-78; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_dns_list_zones"
DESCRIPTION = ("List the DNS zones on a Windows DNS server: zone name, type (primary/secondary/"
               "stub), whether it's AD-integrated, and whether it's a reverse zone. Read-only, "
               "but rides the command engine so it's approval-gated. Give the DNS server's "
               "`server`. Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_dns"
CATEGORY = "write"            # read-only in effect; runs a command on the endpoint
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the DNS server's machine name/AgentId"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, **_: Any):
    from . import _kaseya_common as k
    cmd = ("try { Import-Module DnsServer; Get-DnsServerZone | Select-Object ZoneName,ZoneType,"
           "IsDsIntegrated,IsReverseLookupZone,IsAutoCreated | Format-Table -AutoSize | "
           "Out-String -Width 4096 } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "read-only — read the zone list with kaseya_command_output"
    return out
