"""Set/create a Windows registry value on a machine (D-80; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_registry_set"
DESCRIPTION = ("Create or change a Windows registry value on a machine (the key path is created if "
               "missing). Give the `server`, the `key`, the `value_name` (empty = the key's "
               "default value), the `type` (dword, qword, string, expandstring, multistring), and "
               "the value (`value` for single types, `values` for multistring). NOTE: the agent "
               "runs as SYSTEM. Remove with kaseya_registry_delete_value. Read the result with "
               "kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_registry"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine to change (name/AgentId)"},
        "key": {"type": "string", "description": "the registry key (HKLM/HKCU/HKCR/HKU/HKCC \\ path)"},
        "value_name": {"type": "string", "description": "the value name (empty = default value)"},
        "type": {"type": "string", "enum": ["dword", "qword", "string", "expandstring", "multistring"],
                 "description": "the value type"},
        "value": {"type": "string", "description": "the value (for single types)"},
        "values": {"type": "array", "items": {"type": "string"},
                   "description": "the values (for multistring)"},
    },
    "required": ["server", "key", "value_name", "type"],
    "additionalProperties": False,
}


def run(ctx, server: str, key: str, value_name: str, type: str, value: str = "",
        values: Any = None, **_: Any):
    from . import _kaseya_common as k
    path, err = k.reg_path(key)
    if err:
        return {"ok": False, "error": err}
    vn = str(value_name or "")
    if vn and k.clean_text(vn, 255) is None:
        return {"ok": False, "error": "the value_name is not valid"}
    name = vn if vn else "(default)"
    tp, rendered, verr = k.reg_type_value(type, value, values)
    if verr:
        return {"ok": False, "error": verr}

    pq = k.ps_quote(path)
    cmd = ("try { if (-not (Test-Path -LiteralPath " + pq + ")) { New-Item -Path " + pq +
           " -Force | Out-Null }; New-ItemProperty -LiteralPath " + pq + " -Name " +
           k.ps_quote(name) + " -PropertyType " + tp + " -Value " + rendered +
           " -Force | Out-Null; 'OK: set " + tp + " value' } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out.update({"key": path, "value_name": name, "type": tp})
        out["note"] = "set submitted — confirm with kaseya_command_output"
    return out
