"""Uninstall software on a machine (D-81; SOP: kaseya-vsa). Counterpart to kaseya_install_software."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_uninstall_software"
DESCRIPTION = ("Uninstall an application from a machine. Give the `server` and the `app` name (as "
               "it appears in Programs & Features, e.g. 'Google Chrome'). Tries Chocolatey first "
               "(if the app was installed that way), then the system package list. Read the result "
               "with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_command"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_APP_RE = re.compile(r'^[A-Za-z0-9 ._+()-]{1,128}$')
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine (name/AgentId)"},
        "app": {"type": "string", "description": "the application name to uninstall"},
        "choco_id": {"type": "string", "description": "the Chocolatey package id, if known (optional)"},
    },
    "required": ["server", "app"],
    "additionalProperties": False,
}


def run(ctx, server: str, app: str, choco_id: str = "", **_: Any):
    from . import _kaseya_common as k
    name = (app or "").strip()
    if not _APP_RE.match(name):
        return {"ok": False, "error": "the app name has invalid characters"}
    cid = (choco_id or "").strip()
    if cid and not re.match(r'^[A-Za-z0-9._-]{1,128}$', cid):
        return {"ok": False, "error": "the choco_id has invalid characters"}
    choco_target = cid if cid else name
    # Try Chocolatey if present; otherwise PackageManagement (Get-Package | Uninstall-Package).
    cmd = ("try { $done = $false; "
           "if (Get-Command choco -ErrorAction SilentlyContinue) { "
           "$o = (choco uninstall " + k.ps_quote(choco_target) + " -y --limit-output 2>&1 | "
           "Out-String); if ($LASTEXITCODE -eq 0) { $done = $true; $o = \"[choco] \" + $o } } "
           "else { $o = '' }; "
           "if (-not $done) { $p = Get-Package -Name " + k.ps_quote('*' + name + '*') +
           " -ErrorAction SilentlyContinue; if ($p) { $p | Uninstall-Package -Force "
           "-ErrorAction Stop | Out-Null; $o = \"[pkg] uninstalled \" + ($p.Name -join ', ') } "
           "else { $o = \"Not found: " + name.replace('\"', '') + "\" } }; $o } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["app"] = name
        out["note"] = "uninstall submitted — confirm with kaseya_command_output"
    return out
