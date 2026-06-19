"""Network configuration + active connections on a machine (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_diag_network"
DESCRIPTION = ("Show a machine's network configuration (ipconfig /all — addresses, gateway, DNS) "
               "plus its current established TCP connections. Read-only. Give the `server`. Read "
               "the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_diag"
CATEGORY = "write"            # read-only in effect; runs a command on the endpoint
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine to inspect (name/AgentId)"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, **_: Any):
    from . import _kaseya_common as k
    cmd = ("try { \"=== IP CONFIG ===`n\" + (ipconfig /all | Out-String) + "
           "\"`n=== ESTABLISHED TCP CONNECTIONS ===`n\" + "
           "(Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | "
           "Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,OwningProcess | "
           "Format-Table -AutoSize | Out-String -Width 4096) } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "read-only — read the network report with kaseya_command_output"
    return out
