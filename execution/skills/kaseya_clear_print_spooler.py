"""Clear the print spooler — fix stuck print jobs (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_clear_print_spooler"
DESCRIPTION = ("Fix a stuck print queue: stop the Spooler service, delete all queued jobs, and "
               "start it again. Give the `server` (the machine with the stuck queue). Read the "
               "result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
CATEGORY = "write"
RISK_LEVEL = "low"
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
    cmd = ("try { Stop-Service Spooler -Force -ErrorAction Stop; "
           "Remove-Item 'C:\\Windows\\System32\\spool\\PRINTERS\\*' -Force -ErrorAction "
           "SilentlyContinue; Start-Service Spooler; 'OK: print spooler cleared and restarted' } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "submitted — confirm with kaseya_command_output"
    return out
