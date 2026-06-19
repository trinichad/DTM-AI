"""Reboot a machine through Kaseya (D-71; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_reboot_machine"
DESCRIPTION = ("REBOOT a machine (forced restart). By default waits 60 seconds so a logged-in "
               "user gets a moment. HIGH RISK on a server or a machine someone's working on — "
               "confirm with the user first. Runs through the Command Toolkit engine.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
        "delay_seconds": {"type": "integer",
                          "description": "seconds before the reboot (default 60, 0 = immediate, "
                                         "max 3600)"},
    },
    "required": ["machine"],
    "additionalProperties": False,
}


def run(ctx, machine: str, delay_seconds: int = 60, **_: Any):
    from . import _kaseya_common as k
    try:
        delay = max(0, min(int(delay_seconds if delay_seconds is not None else 60), 3600))
    except (TypeError, ValueError):
        delay = 60
    command = f"shutdown /r /f /t {delay} /c \"Reboot scheduled by IT\""
    out = k.run_command(ctx, machine, command)
    if out.get("ok"):
        out["rebooting_in_seconds"] = delay
        out["note"] = (f"reboot scheduled in {delay}s (cancel within that window with "
                       f"'shutdown /a' via kaseya_run_command if needed)")
    return out
