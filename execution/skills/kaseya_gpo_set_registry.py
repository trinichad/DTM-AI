"""Set a registry-backed (Administrative Template) policy value in a GPO (D-77 follow-up)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_gpo_set_registry"
DESCRIPTION = ("Set a registry-backed Group Policy setting in a GPO — i.e. anything under "
               "Administrative Templates (ADMX). Run against a domain controller (`server`). Give "
               "the GPO `name`, the registry `key` (must be under HKLM\\ or HKCU\\), the "
               "`value_name`, the `type` (dword, qword, string, expandstring, multistring), and "
               "the value (`value` for single types, `values` for multistring). Note: UI-only "
               "policies (password/account, user-rights, scripts, software install) are NOT "
               "registry-backed and can't be set this way. Remove with kaseya_gpo_remove_registry. "
               "Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_gpo"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_KEY_RE = re.compile(r'^(HKLM|HKCU|HKEY_LOCAL_MACHINE|HKEY_CURRENT_USER)\\[A-Za-z0-9_\\ .:-]{1,500}$',
                     re.IGNORECASE)
_VALNAME_RE = re.compile(r'^[A-Za-z0-9_ .:-]{0,255}$')
_TYPES = {"string": "String", "expandstring": "ExpandString", "dword": "DWord",
          "qword": "QWord", "multistring": "MultiString"}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "a domain controller's machine name/AgentId"},
        "name": {"type": "string", "description": "the GPO's display name"},
        "key": {"type": "string", "description": "registry key, e.g. "
                "'HKLM\\Software\\Policies\\Microsoft\\Windows\\WindowsUpdate'"},
        "value_name": {"type": "string", "description": "the value name (empty string = default value)"},
        "type": {"type": "string", "enum": list(_TYPES), "description": "the value type"},
        "value": {"type": "string", "description": "the value (for string/expandstring/dword/qword)"},
        "values": {"type": "array", "items": {"type": "string"},
                   "description": "the values (for multistring)"},
    },
    "required": ["server", "name", "key", "value_name", "type"],
    "additionalProperties": False,
}


def run(ctx, server: str, name: str, key: str, value_name: str, type: str,
        value: str = "", values: Any = None, **_: Any):
    from . import _kaseya_common as k
    nm = k.clean_text(name, 256)
    if not nm:
        return {"ok": False, "error": "give the GPO name"}
    ky = str(key or "").strip()
    if not _KEY_RE.match(ky):
        return {"ok": False, "error": "key must be a registry path under HKLM\\ or HKCU\\"}
    vn = str(value_name or "")
    if not _VALNAME_RE.match(vn):
        return {"ok": False, "error": "the value_name has invalid characters"}
    tp = _TYPES.get(str(type or "").strip().lower())
    if not tp:
        return {"ok": False, "error": "type must be one of: " + ", ".join(_TYPES)}

    if tp in ("DWord", "QWord"):
        try:
            num = int(str(value).strip(), 0)            # accepts 0x.. and decimal
        except (TypeError, ValueError):
            return {"ok": False, "error": f"{tp} needs a whole-number value"}
        vfrag = "-Value " + str(num)
    elif tp == "MultiString":
        items = values if isinstance(values, list) else ([value] if (value or "").strip() else [])
        if not items:
            return {"ok": False, "error": "multistring needs `values` (a list of strings)"}
        vfrag = "-Value " + k._ps_value([str(x) for x in items])   # @('a','b')
    else:                                               # String / ExpandString
        v = k.clean_text(value, 1024)
        if v is None:
            return {"ok": False, "error": "give a valid string value"}
        vfrag = "-Value " + k.ps_quote(v)

    cmd = ("try { Import-Module GroupPolicy; Set-GPRegistryValue -Name " + k.ps_quote(nm) +
           " -Key " + k.ps_quote(ky) + " -ValueName " + k.ps_quote(vn) + " -Type " + tp + " " +
           vfrag + " | Out-Null; 'OK: set " + tp + " value' } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out.update({"gpo": nm, "key": ky, "value_name": vn, "type": tp})
        out["note"] = "set submitted — confirm with kaseya_command_output"
    return out
