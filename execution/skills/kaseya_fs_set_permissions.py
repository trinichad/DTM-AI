"""Set NTFS permissions on a folder (icacls) — generic, any client (D-74; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_fs_set_permissions"
DESCRIPTION = ("Set NTFS permissions on a folder via icacls on a server (any client). Give the "
               "`server` and the `path`. `grant` is a map of principal → access, where access is "
               "a word (full | modify | read | readonly | write) or a raw icacls spec like "
               "'(OI)(CI)M'. `remove` is a list of principals to strip. `disable_inheritance` "
               "true removes inherited permissions first (then applies your grants), matching a "
               "locked-down share. Each grant REPLACES that principal's existing entry. Read the "
               "result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_fs"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

_BAD_PATH = re.compile(r'[<>"|?*]')
# DOMAIN\Group, BUILTIN names, well-knowns: letters/digits/space/backslash/dot/dash/underscore/$
_PRINCIPAL = re.compile(r'^[A-Za-z0-9 ._$\\-]{1,256}$')
_RIGHTS = re.compile(r'^(\((OI|CI|IO|NP|I)\))*(F|M|RX|RW|R|W|RD|WD|D|N)$')
_FRIENDLY = {"full": "(OI)(CI)F", "modify": "(OI)(CI)M", "read": "(OI)(CI)RX",
             "readonly": "(OI)(CI)R", "write": "(OI)(CI)W"}

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "machine name/AgentId with access to the path"},
        "path": {"type": "string", "description": "the folder to set permissions on"},
        "grant": {"type": "object", "additionalProperties": {"type": "string"},
                  "description": "principal → access, e.g. {'ACME\\\\Maple Grove':'modify', "
                                 "'SYSTEM':'full'}"},
        "remove": {"type": "array", "items": {"type": "string"},
                   "description": "principals to remove entirely"},
        "disable_inheritance": {"type": "boolean",
                                "description": "remove inherited permissions first (default false)"},
    },
    "required": ["server", "path"],
    "additionalProperties": False,
}


def run(ctx, server: str, path: str, grant: Any = None, remove: Any = None,
        disable_inheritance: bool = False, **_: Any):
    from . import _kaseya_common as k
    p = k.clean_text(path, 512)
    if not p or _BAD_PATH.search(p):
        return {"ok": False, "error": "give a valid folder path (no < > \" | ? *)"}

    grant_args: list[str] = []
    if isinstance(grant, dict):
        for principal, access in grant.items():
            who = str(principal or "").strip()
            if not _PRINCIPAL.match(who):
                return {"ok": False, "error": f"invalid principal '{principal}'"}
            acc = str(access or "").strip()
            spec = _FRIENDLY.get(acc.lower()) or (acc if _RIGHTS.match(acc) else None)
            if not spec:
                return {"ok": False, "error": (
                    f"invalid access '{access}' for '{who}' — use full/modify/read/readonly/write "
                    "or an icacls spec like (OI)(CI)M")}
            grant_args.append(k.ps_quote(f"{who}:{spec}"))

    remove_args: list[str] = []
    if isinstance(remove, list):
        for principal in remove:
            who = str(principal or "").strip()
            if not who:
                continue
            if not _PRINCIPAL.match(who):
                return {"ok": False, "error": f"invalid principal '{principal}'"}
            remove_args.append(k.ps_quote(who))

    if not (grant_args or remove_args or disable_inheritance):
        return {"ok": False, "error": "give something to do: grant, remove, or disable_inheritance"}

    lines = ["try {", "  $path = " + k.ps_quote(p),
             "  if (-not (Test-Path -LiteralPath $path)) { throw \"Path not found: $path\" }"]
    if disable_inheritance:
        lines.append("  icacls $path /inheritance:r | Out-Null")
    if grant_args:
        lines.append("  icacls $path /grant:r " + " ".join(grant_args) + " | Out-Null")
    if remove_args:
        lines.append("  icacls $path /remove " + " ".join(remove_args) + " | Out-Null")
    lines += ["  if ($LASTEXITCODE -ne 0) { \"WARN: icacls exit $LASTEXITCODE on $path\" } "
              "else { \"OK: permissions applied on $path\" }",
              "} catch { 'ERROR: ' + $_.Exception.Message }"]
    cmd = "\n".join(lines)

    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["path"] = p
        out["granted"] = list(grant.keys()) if isinstance(grant, dict) else []
        out["removed"] = [str(x) for x in remove] if isinstance(remove, list) else []
        out["inheritance_disabled"] = bool(disable_inheritance)
        out["note"] = "submitted — confirm with kaseya_command_output"
    return out
