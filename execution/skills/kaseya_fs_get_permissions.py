"""Report a folder's current NTFS permissions (owner + ACL) — generic, any client (D-75)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_fs_get_permissions"
DESCRIPTION = ("Show a folder's CURRENT NTFS permissions on a server (any client): owner, whether "
               "inheritance is disabled, and every access entry (who, allow/deny, rights, "
               "inherited or explicit). Read-only — makes no changes — but rides the command "
               "engine so it's approval-gated like the other command tools. Give the `server` and "
               "the `path`. Use it before/after kaseya_fs_set_permissions to verify. Read the "
               "result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_fs"
CATEGORY = "write"            # read-only in effect, but runs a command on the endpoint (gated like all command tools)
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

_BAD_PATH = re.compile(r'[<>"|?*]')

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "machine name/AgentId with access to the path"},
        "path": {"type": "string", "description": "the folder to report permissions for"},
    },
    "required": ["server", "path"],
    "additionalProperties": False,
}


def run(ctx, server: str, path: str, **_: Any):
    from . import _kaseya_common as k
    p = k.clean_text(path, 512)
    if not p or _BAD_PATH.search(p):
        return {"ok": False, "error": "give a valid folder path (no < > \" | ? *)"}

    cmd = _PS.replace("@@PATH@@", k.ps_quote(p))
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["path"] = p
        out["note"] = "read-only — confirm the ACL with kaseya_command_output"
    return out


# Get-Acl for owner + the inheritance-protected flag; the .Access rules listed one per line.
# Only the path is user input, injected once via ps_quote — injection-safe.
_PS = r"""try {
  $path = @@PATH@@
  if (-not (Test-Path -LiteralPath $path)) { throw "Path not found: $path" }
  $acl = Get-Acl -LiteralPath $path
  $inh = if ($acl.AreAccessRulesProtected) { 'DISABLED (inheritance blocked)' } else { 'enabled' }
  $out = "Path        : $path`nOwner       : $($acl.Owner)`nInheritance : $inh`n`nPermissions:`n"
  $out += ($acl.Access | ForEach-Object {
    "  {0,-32} {1,-6} {2} (inherited={3})" -f $_.IdentityReference, $_.AccessControlType, $_.FileSystemRights, $_.IsInherited
  } | Out-String)
  $out
} catch { 'ERROR: ' + $_.Exception.Message }"""
