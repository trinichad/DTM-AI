"""Delete a Windows registry value on a machine (D-80). Opposite of kaseya_registry_set."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_registry_delete_value"
DESCRIPTION = ("Delete a single Windows registry value on a machine (the key itself stays). Give "
               "the `server`, the `key`, and the `value_name` to remove. To delete a whole key "
               "and its subkeys, use kaseya_registry_delete_key. Read the result with "
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
        "value_name": {"type": "string", "description": "the value to remove"},
    },
    "required": ["server", "key", "value_name"],
    "additionalProperties": False,
}


def run(ctx, server: str, key: str, value_name: str, **_: Any):
    from . import _kaseya_common as k
    path, err = k.reg_path(key)
    if err:
        return {"ok": False, "error": err}
    vn = k.clean_text(value_name, 255)
    if vn is None:
        return {"ok": False, "error": "give the value name to remove"}
    pq = k.ps_quote(path)
    cmd = ("try { Remove-ItemProperty -LiteralPath " + pq + " -Name " + k.ps_quote(vn) +
           " -Force; 'OK: removed value' } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out.update({"key": path, "value_name": vn})
        out["note"] = "remove submitted — confirm with kaseya_command_output"
    return out
