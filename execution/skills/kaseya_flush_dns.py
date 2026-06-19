"""Flush the DNS resolver cache on a machine (D-71; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_flush_dns"
DESCRIPTION = ("Flush the DNS resolver cache on a machine (common fix for 'can't reach a site "
               "after a DNS change'). Runs `ipconfig /flushdns` through the Command Toolkit "
               "engine.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
    },
    "required": ["machine"],
    "additionalProperties": False,
}


def run(ctx, machine: str, **_: Any):
    from . import _kaseya_common as k
    out = k.run_command(ctx, machine, "ipconfig /flushdns")
    if out.get("ok"):
        out["note"] = "DNS cache flush submitted — confirm with kaseya_command_output"
    return out
