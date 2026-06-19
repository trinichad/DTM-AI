"""Release + renew the DHCP lease on a machine (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_renew_ip"
DESCRIPTION = ("Release and renew a machine's DHCP IP lease (ipconfig /release + /renew) and flush "
               "DNS — for 'got a bad/APIPA address' situations. Give the `server`. (Briefly drops "
               "the network while it renews.) Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine (name/AgentId)"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, **_: Any):
    from . import _kaseya_common as k
    cmd = ("try { ipconfig /release | Out-Null; $r = (ipconfig /renew | Out-String); "
           "ipconfig /flushdns | Out-Null; "
           "$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
           "Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' } | "
           "Select-Object -ExpandProperty IPAddress) -join ', '; "
           "\"OK: lease renewed. Current IPv4: $ip\" } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "renew submitted — confirm with kaseya_command_output"
    return out
