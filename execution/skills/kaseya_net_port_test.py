"""Test TCP connectivity to a host:port from a machine (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_net_port_test"
DESCRIPTION = ("From a machine, test whether it can reach a host on a TCP port (Test-NetConnection) "
               "— 'can this PC actually reach the mail/SQL/RDP server?'. Give the `server` (where "
               "the test runs FROM), the `host`, and the `port`. Read-only. Read the result with "
               "kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_net"
CATEGORY = "write"            # read-only in effect; runs a command on the endpoint
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_HOST_RE = re.compile(r'^[A-Za-z0-9._-]{1,253}$')
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine to test FROM (name/AgentId)"},
        "host": {"type": "string", "description": "the destination host name or IP"},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535, "description": "the TCP port"},
    },
    "required": ["server", "host", "port"],
    "additionalProperties": False,
}


def run(ctx, server: str, host: str, port: Any, **_: Any):
    from . import _kaseya_common as k
    h = (host or "").strip()
    if not _HOST_RE.match(h):
        return {"ok": False, "error": "give a valid host name or IP"}
    try:
        p = int(port)
    except (TypeError, ValueError):
        return {"ok": False, "error": "port must be a number"}
    if not 1 <= p <= 65535:
        return {"ok": False, "error": "port must be between 1 and 65535"}
    cmd = ("try { Test-NetConnection -ComputerName " + k.ps_quote(h) + " -Port " + str(p) +
           " -WarningAction SilentlyContinue | Select-Object ComputerName,RemoteAddress,"
           "RemotePort,TcpTestSucceeded,PingSucceeded | Format-List | Out-String } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out.update({"host": h, "port": p})
        out["note"] = "read-only — TcpTestSucceeded=True means the port is reachable"
    return out
