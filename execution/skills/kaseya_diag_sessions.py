"""Logged-on user sessions on a machine — quser (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_diag_sessions"
DESCRIPTION = ("Show who is logged on to a machine — active and disconnected sessions, with idle "
               "time (quser). Useful before a reboot, or to find a stuck disconnected session on "
               "a terminal/RDS server. Read-only. Give the `server`. Read the result with "
               "kaseya_command_output.")
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
    cmd = ("try { $r = (quser 2>&1 | Out-String); if ($r.Trim()) { $r } "
           "else { 'No interactive sessions.' } } "
           "catch { 'No interactive sessions / quser unavailable.' }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "read-only — read the session list with kaseya_command_output"
    return out
