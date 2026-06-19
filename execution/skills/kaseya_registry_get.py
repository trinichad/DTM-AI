"""Read the Windows registry on a machine (D-80; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_registry_get"
DESCRIPTION = ("Read the Windows registry on a machine. Give the `server` (the machine) and the "
               "`key` (e.g. 'HKLM\\Software\\Microsoft\\Windows\\CurrentVersion'). Pass "
               "`value_name` to read one value; omit it to list every value AND subkey under the "
               "key. Read-only. NOTE: the agent runs as SYSTEM, so 'HKCU' is SYSTEM's profile, not "
               "the logged-in user's. Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_registry"
CATEGORY = "write"            # read-only in effect; runs a command on the endpoint
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine to read (name/AgentId)"},
        "key": {"type": "string", "description": "the registry key (HKLM/HKCU/HKCR/HKU/HKCC \\ path)"},
        "value_name": {"type": "string", "description": "a single value to read (optional)"},
    },
    "required": ["server", "key"],
    "additionalProperties": False,
}


def run(ctx, server: str, key: str, value_name: str = "", **_: Any):
    from . import _kaseya_common as k
    path, err = k.reg_path(key)
    if err:
        return {"ok": False, "error": err}
    pq = k.ps_quote(path)
    guard = ("if (-not (Test-Path -LiteralPath " + pq + ")) { throw 'Key not found: " +
             path.replace("'", "") + "' }; ")
    if (value_name or "").strip():
        vn = k.clean_text(value_name, 255)
        if vn is None:
            return {"ok": False, "error": "the value_name is not valid"}
        body = ("(Get-ItemPropertyValue -LiteralPath " + pq + " -Name " + k.ps_quote(vn) +
                " | Out-String)")
    else:
        body = ("\"Values:`n\" + ((Get-ItemProperty -LiteralPath " + pq + " | Select-Object * "
                "-Exclude PS* | Format-List | Out-String)) + \"`nSubkeys:`n\" + "
                "((Get-ChildItem -LiteralPath " + pq + " -ErrorAction SilentlyContinue | "
                "Select-Object -ExpandProperty PSChildName | Out-String))")
    cmd = "try { " + guard + body + " } catch { 'ERROR: ' + $_.Exception.Message }"
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["key"] = path
        out["note"] = "read-only — read the value(s) with kaseya_command_output"
    return out
