"""Trace the network path to a host from a machine (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_net_traceroute"
DESCRIPTION = ("Trace the network route from a machine to a destination (tracert) — shows where "
               "the path slows down or breaks. Give the `server` (where it runs FROM) and the "
               "`host`. Read-only. Read the result with kaseya_command_output.")
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
        "server": {"type": "string", "description": "the machine to trace FROM (name/AgentId)"},
        "host": {"type": "string", "description": "the destination host name or IP"},
        "max_hops": {"type": "integer", "minimum": 1, "maximum": 60,
                     "description": "maximum hops (default 20)"},
    },
    "required": ["server", "host"],
    "additionalProperties": False,
}


def run(ctx, server: str, host: str, max_hops: Any = None, **_: Any):
    from . import _kaseya_common as k
    h = (host or "").strip()
    if not _HOST_RE.match(h):
        return {"ok": False, "error": "give a valid host name or IP"}
    hops = 20
    if max_hops is not None:
        try:
            hops = int(max_hops)
        except (TypeError, ValueError):
            return {"ok": False, "error": "max_hops must be a number"}
        if not 1 <= hops <= 60:
            return {"ok": False, "error": "max_hops must be between 1 and 60"}
    cmd = ("try { (tracert -d -h " + str(hops) + " " + k.ps_quote(h) + " | Out-String) } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["host"] = h
        out["note"] = "read-only — read the trace with kaseya_command_output"
    return out
