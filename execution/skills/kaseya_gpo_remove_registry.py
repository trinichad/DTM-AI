"""Remove a registry-backed policy value from a GPO (D-77 follow-up). Opposite of set_registry."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_gpo_remove_registry"
DESCRIPTION = ("Remove a registry-backed setting from a GPO. Run against a domain controller "
               "(`server`). Give the GPO `name` and the registry `key`; pass `value_name` to "
               "remove a single value, or omit it to remove every value under that key. Read the "
               "result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_gpo"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_KEY_RE = re.compile(r'^(HKLM|HKCU|HKEY_LOCAL_MACHINE|HKEY_CURRENT_USER)\\[A-Za-z0-9_\\ .:-]{1,500}$',
                     re.IGNORECASE)
_VALNAME_RE = re.compile(r'^[A-Za-z0-9_ .:-]{1,255}$')
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "a domain controller's machine name/AgentId"},
        "name": {"type": "string", "description": "the GPO's display name"},
        "key": {"type": "string", "description": "registry key under HKLM\\ or HKCU\\"},
        "value_name": {"type": "string", "description": "a single value to remove (optional; omit "
                                                        "to clear the whole key)"},
    },
    "required": ["server", "name", "key"],
    "additionalProperties": False,
}


def run(ctx, server: str, name: str, key: str, value_name: str = "", **_: Any):
    from . import _kaseya_common as k
    nm = k.clean_text(name, 256)
    if not nm:
        return {"ok": False, "error": "give the GPO name"}
    ky = str(key or "").strip()
    if not _KEY_RE.match(ky):
        return {"ok": False, "error": "key must be a registry path under HKLM\\ or HKCU\\"}
    parts = ["Remove-GPRegistryValue", "-Name", k.ps_quote(nm), "-Key", k.ps_quote(ky)]
    if (value_name or "").strip():
        vn = str(value_name).strip()
        if not _VALNAME_RE.match(vn):
            return {"ok": False, "error": "the value_name has invalid characters"}
        parts += ["-ValueName", k.ps_quote(vn)]
    cmd = ("try { Import-Module GroupPolicy; " + " ".join(parts) +
           " | Out-Null; 'OK: removed registry setting' } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out.update({"gpo": nm, "key": ky})
        out["note"] = "remove submitted — confirm with kaseya_command_output"
    return out
