"""Back up a GPO to a folder (D-77; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_gpo_backup"
DESCRIPTION = ("Back up a Group Policy Object to a folder (so it can be restored or migrated). Run "
               "against a domain controller (`server`). Give the GPO `name` and a `path` folder "
               "to write the backup into. Optional comment. Read the result with "
               "kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_gpo"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_BAD_PATH = re.compile(r'[<>"|?*]')
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "a domain controller's machine name/AgentId"},
        "name": {"type": "string", "description": "the GPO's display name"},
        "path": {"type": "string", "description": "an existing folder to write the backup into"},
        "comment": {"type": "string", "description": "a backup comment (optional)"},
    },
    "required": ["server", "name", "path"],
    "additionalProperties": False,
}


def run(ctx, server: str, name: str, path: str, comment: str = "", **_: Any):
    from . import _kaseya_common as k
    nm = k.clean_text(name, 256)
    p = k.clean_text(path, 512)
    if not nm:
        return {"ok": False, "error": "give the GPO name"}
    if not p or _BAD_PATH.search(p):
        return {"ok": False, "error": "give a valid backup folder path (no < > \" | ? *)"}
    parts = ["Backup-GPO", "-Name", k.ps_quote(nm), "-Path", k.ps_quote(p)]
    if (comment or "").strip():
        c = k.clean_text(comment, 512)
        if not c:
            return {"ok": False, "error": "the comment is not valid"}
        parts += ["-Comment", k.ps_quote(c)]
    cmd = ("try { Import-Module GroupPolicy; if (-not (Test-Path -LiteralPath " + k.ps_quote(p) +
           ")) { throw 'Backup folder not found' }; " + " ".join(parts) +
           " | Select-Object DisplayName,Id,BackupDirectory | Format-List | Out-String } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["gpo"] = nm
        out["path"] = p
        out["note"] = "backup submitted — confirm with kaseya_command_output"
    return out
