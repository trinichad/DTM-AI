"""Create a folder structure on a server — generic, any client (D-74; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_fs_provision_folders"
DESCRIPTION = ("Create a folder on a server (any client), optionally cloning the sub-folder "
               "structure of a SAMPLE/template tree (folders only, no files). Give the `server` "
               "(a machine with access to the path) and the `target` folder to create. Pass "
               "`sample_dir` to copy a template tree's folders into the target. ABORTS without "
               "changing anything if the target already exists. Lock it down with "
               "kaseya_fs_set_permissions. Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_fs"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

_BAD = re.compile(r'[<>"|?*]')        # path-illegal chars (\\ : / kept — they're path separators)

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "machine name/AgentId with access to the path"},
        "target": {"type": "string",
                   "description": "the folder to create (UNC or local), e.g. "
                                  "'\\\\fs01\\Share\\New Client'"},
        "sample_dir": {"type": "string",
                       "description": "optional template tree whose sub-folders are cloned into "
                                      "the target (folders only)"},
    },
    "required": ["server", "target"],
    "additionalProperties": False,
}


def run(ctx, server: str, target: str, sample_dir: str = "", **_: Any):
    from . import _kaseya_common as k
    tgt = k.clean_text(target, 512)
    if not tgt or _BAD.search(tgt):
        return {"ok": False, "error": "give a valid target folder path (no < > \" | ? *)"}
    sample = ""
    if (sample_dir or "").strip():
        sample = k.clean_text(sample_dir, 512)
        if not sample or _BAD.search(sample):
            return {"ok": False, "error": "the sample folder path is not valid"}

    lines = ["try {",
             "  $target = " + k.ps_quote(tgt),
             "  if (Test-Path -LiteralPath $target) { throw \"Target already exists: $target "
             "(nothing changed)\" }"]
    if sample:
        lines += [
            "  $sample = " + k.ps_quote(sample),
            "  if (-not (Test-Path -LiteralPath $sample)) { throw \"Sample tree not found: "
            "$sample\" }",
            "  robocopy $sample $target /E /XF * /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null",
            "  if ($LASTEXITCODE -ge 8) { throw \"robocopy failed (code $LASTEXITCODE)\" }",
            "  \"OK: cloned folder tree into $target\""]
    else:
        lines += [
            "  New-Item -ItemType Directory -Path $target -Force | Out-Null",
            "  \"OK: created folder $target\""]
    lines += ["} catch { 'ERROR: ' + $_.Exception.Message }"]
    cmd = "\n".join(lines)

    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["target"] = tgt
        out["cloned_from"] = sample or None
        out["note"] = "submitted — confirm with kaseya_command_output (aborts if target exists)"
    return out
