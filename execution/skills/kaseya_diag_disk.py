"""Disk space + biggest folders on a machine (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_diag_disk"
DESCRIPTION = ("Show a machine's drives (size/free/percent-free) and the biggest top-level folders "
               "on a drive ('what's eating the disk?'). Read-only. Give the `server`; optional "
               "`path` to scan for big folders (default C:\\). Note: the folder scan can take a "
               "minute on large drives. Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_diag"
CATEGORY = "write"            # read-only in effect; runs a command on the endpoint
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_BAD = re.compile(r'[<>"|?*]')
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine to inspect (name/AgentId)"},
        "path": {"type": "string", "description": "drive/folder to scan for big folders (default C:\\)"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, path: str = "", **_: Any):
    from . import _kaseya_common as k
    scan = k.clean_text(path, 512) if (path or "").strip() else "C:\\"
    if not scan or _BAD.search(scan):
        return {"ok": False, "error": "give a valid path to scan (no < > \" | ? *)"}
    cmd = ("try { $scan = " + k.ps_quote(scan) + "; "
           "$vol = Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object "
           "DeviceID,@{N='Size(GB)';E={[math]::Round($_.Size/1GB,1)}},"
           "@{N='Free(GB)';E={[math]::Round($_.FreeSpace/1GB,1)}},"
           "@{N='Free%';E={if($_.Size){[math]::Round($_.FreeSpace/$_.Size*100)}else{0}}}; "
           "$big = Get-ChildItem -LiteralPath $scan -Directory -ErrorAction SilentlyContinue | "
           "ForEach-Object { $s = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction "
           "SilentlyContinue | Measure-Object Length -Sum).Sum; [PSCustomObject]@{Folder=$_.Name; "
           "'Size(GB)'=[math]::Round(($s/1GB),2)} } | Sort-Object 'Size(GB)' -Descending | "
           "Select-Object -First 10; "
           "\"=== VOLUMES ===`n\" + ($vol | Format-Table -AutoSize | Out-String) + "
           "\"`n=== BIGGEST FOLDERS in $scan ===`n\" + ($big | Format-Table -AutoSize | Out-String) } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "read-only — read the disk report with kaseya_command_output"
    return out
