"""Delete a Windows registry KEY and all its subkeys on a machine (D-80) — DESTRUCTIVE."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_registry_delete_key"
DESCRIPTION = ("Delete a Windows registry KEY and EVERYTHING under it (all values + subkeys) on a "
               "machine — recursive and irreversible. Give the `server` and the `key`. This is "
               "destructive, so it always needs a per-action approval. For a single value use "
               "kaseya_registry_delete_value instead. Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_registry"
CATEGORY = "destructive"     # recursive key removal — always requires per-action approval (Rule #1 floor)
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine to change (name/AgentId)"},
        "key": {"type": "string", "description": "the registry key to delete (HKLM/HKCU/HKCR/HKU/HKCC \\ path)"},
    },
    "required": ["server", "key"],
    "additionalProperties": False,
}


def run(ctx, server: str, key: str, **_: Any):
    from . import _kaseya_common as k
    path, err = k.reg_path(key)
    if err:
        return {"ok": False, "error": err}
    # refuse a bare hive root (e.g. HKLM with no subpath) — far too broad to ever be intended
    if "\\" not in path.split("::", 1)[1]:
        return {"ok": False, "error": "refusing to delete a whole hive root — give a specific key path"}
    pq = k.ps_quote(path)
    cmd = ("try { if (-not (Test-Path -LiteralPath " + pq + ")) { throw 'Key not found' }; "
           "Remove-Item -LiteralPath " + pq + " -Recurse -Force; 'OK: removed key' } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["key"] = path
        out["note"] = "delete submitted — confirm with kaseya_command_output"
    return out
