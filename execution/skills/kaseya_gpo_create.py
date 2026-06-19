"""Create a new (empty) GPO (D-77 follow-up; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_gpo_create"
DESCRIPTION = ("Create a new, empty Group Policy Object. Run against a domain controller "
               "(`server`) and give the GPO `name` (optional comment). It is NOT linked anywhere "
               "yet — link it with kaseya_gpo_link and add settings with kaseya_gpo_set_registry. "
               "Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_gpo"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "a domain controller's machine name/AgentId"},
        "name": {"type": "string", "description": "the new GPO's name"},
        "comment": {"type": "string", "description": "a comment/description (optional)"},
    },
    "required": ["server", "name"],
    "additionalProperties": False,
}


def run(ctx, server: str, name: str, comment: str = "", **_: Any):
    from . import _kaseya_common as k
    nm = k.clean_text(name, 256)
    if not nm:
        return {"ok": False, "error": "give the new GPO's name"}
    parts = ["New-GPO", "-Name", k.ps_quote(nm)]
    if (comment or "").strip():
        c = k.clean_text(comment, 512)
        if not c:
            return {"ok": False, "error": "the comment is not valid"}
        parts += ["-Comment", k.ps_quote(c)]
    cmd = ("try { Import-Module GroupPolicy; " + " ".join(parts) +
           " | Select-Object DisplayName,Id,GpoStatus | Format-List | Out-String } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["gpo"] = nm
        out["note"] = "create submitted — confirm with kaseya_command_output (not linked yet)"
    return out
