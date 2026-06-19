"""Free disk space — clear temp, recycle bin, Windows Update cache (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_disk_cleanup"
DESCRIPTION = ("Free up disk space on a machine: clear the Windows + user TEMP folders, empty the "
               "Recycle Bin, and clear the Windows Update download cache. Reports free space "
               "before and after. Give the `server`. Read the result with kaseya_command_output.")
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

# Bounded to known-safe temp/cache locations only (never touches user documents).
_PS = r"""try {
  function FreeGB { [math]::Round((Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'").FreeSpace/1GB,2) }
  $before = FreeGB
  Remove-Item "$env:windir\Temp\*" -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item "$env:TEMP\*" -Recurse -Force -ErrorAction SilentlyContinue
  try { Clear-RecycleBin -Force -ErrorAction SilentlyContinue } catch {}
  try {
    Stop-Service wuauserv -Force -ErrorAction SilentlyContinue
    Remove-Item "$env:windir\SoftwareDistribution\Download\*" -Recurse -Force -ErrorAction SilentlyContinue
    Start-Service wuauserv -ErrorAction SilentlyContinue
  } catch {}
  $after = FreeGB
  "OK: cleanup done. Free C: before=$before GB, after=$after GB, recovered=$([math]::Round($after-$before,2)) GB"
} catch { 'ERROR: ' + $_.Exception.Message }"""


def run(ctx, server: str, **_: Any):
    from . import _kaseya_common as k
    out = k.run_command(ctx, server, _PS)
    if out.get("ok"):
        out["note"] = "cleanup submitted — confirm with kaseya_command_output"
    return out
