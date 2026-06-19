"""Link a GPO to an OU (D-77; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_gpo_link"
DESCRIPTION = ("Link a Group Policy Object to an OU (or domain/site) so it applies there. Run "
               "against a domain controller (`server`). Give the GPO `name` and the `target` "
               "distinguished name (e.g. 'OU=Staff,DC=acme,DC=local'). Optional: enabled "
               "(default true) and enforced (default false). Unlink with kaseya_gpo_unlink. Read "
               "the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_gpo"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "a domain controller's machine name/AgentId"},
        "name": {"type": "string", "description": "the GPO's display name"},
        "target": {"type": "string", "description": "the OU/domain/site distinguished name to link to"},
        "enabled": {"type": "boolean", "description": "is the link enabled (default true)"},
        "enforced": {"type": "boolean", "description": "is the link enforced/no-override (default false)"},
    },
    "required": ["server", "name", "target"],
    "additionalProperties": False,
}


def run(ctx, server: str, name: str, target: str, enabled: bool = True,
        enforced: bool = False, **_: Any):
    from . import _kaseya_common as k
    nm = k.clean_text(name, 256)
    tgt = k.clean_text(target, 512)
    if not (nm and tgt):
        return {"ok": False, "error": "give the GPO name and the target distinguished name"}
    link = "Yes" if enabled else "No"
    enf = "Yes" if enforced else "No"
    cmd = ("try { Import-Module GroupPolicy; New-GPLink -Name " + k.ps_quote(nm) + " -Target " +
           k.ps_quote(tgt) + " -LinkEnabled " + link + " -Enforced " + enf +
           " | Out-Null; 'OK: linked GPO to the target' } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["gpo"] = nm
        out["target"] = tgt
        out["note"] = "link submitted — confirm with kaseya_command_output"
    return out
