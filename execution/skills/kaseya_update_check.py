"""Check Windows Update status — pending + recently installed (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_update_check"
DESCRIPTION = ("Check Windows Update on a machine: how many updates are pending (with titles) and "
               "the recently installed ones. Read-only. Uses the built-in Windows Update API (no "
               "extra module needed). Give the `server`. Read the result with "
               "kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_update"
CATEGORY = "write"            # read-only in effect; runs a command on the endpoint
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

_PS = r"""try {
  $searcher = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
  $pending = $searcher.Search("IsInstalled=0 AND IsHidden=0").Updates
  $titles = @($pending | ForEach-Object { " - " + $_.Title }) -join "`n"
  $recent = Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 10 HotFixID,Description,InstalledOn | Format-Table -AutoSize | Out-String
  "Pending updates: " + @($pending).Count + "`n" + $titles + "`n`n=== RECENTLY INSTALLED ===`n" + $recent
} catch { 'ERROR: ' + $_.Exception.Message }"""


def run(ctx, server: str, **_: Any):
    from . import _kaseya_common as k
    out = k.run_command(ctx, server, _PS)
    if out.get("ok"):
        out["note"] = "read-only — read the update status with kaseya_command_output"
    return out
