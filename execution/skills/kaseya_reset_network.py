"""Reset the network stack — winsock + IP reset (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_reset_network"
DESCRIPTION = ("Reset a machine's TCP/IP network stack (netsh winsock reset + netsh int ip reset) "
               "and flush DNS — the 'nuclear' fix for broken networking. A REBOOT is required "
               "afterward for it to take full effect. Give the `server`. Read the result with "
               "kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
CATEGORY = "write"
RISK_LEVEL = "high"
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
    cmd = ("try { $a = (netsh winsock reset | Out-String); $b = (netsh int ip reset | Out-String); "
           "ipconfig /flushdns | Out-Null; $a + $b + \"`nOK: network stack reset — a REBOOT is "
           "required to finish.\" } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = ("reset submitted — REBOOT required afterward (use kaseya_reboot_machine); "
                       "confirm with kaseya_command_output")
    return out
