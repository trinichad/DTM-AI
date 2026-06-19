"""Unlink a GPO from an OU (D-77; SOP: kaseya-vsa). The opposite of kaseya_gpo_link."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_gpo_unlink"
DESCRIPTION = ("Remove a Group Policy link from an OU (or domain/site) so the GPO no longer "
               "applies there (the GPO itself is NOT deleted). Run against a domain controller "
               "(`server`). Give the GPO `name` and the `target` distinguished name. Read the "
               "result with kaseya_command_output.")
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
        "target": {"type": "string", "description": "the OU/domain/site distinguished name to unlink from"},
    },
    "required": ["server", "name", "target"],
    "additionalProperties": False,
}


def run(ctx, server: str, name: str, target: str, **_: Any):
    from . import _kaseya_common as k
    nm = k.clean_text(name, 256)
    tgt = k.clean_text(target, 512)
    if not (nm and tgt):
        return {"ok": False, "error": "give the GPO name and the target distinguished name"}
    cmd = ("try { Import-Module GroupPolicy; Remove-GPLink -Name " + k.ps_quote(nm) + " -Target " +
           k.ps_quote(tgt) + " | Out-Null; 'OK: unlinked GPO from the target' } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["gpo"] = nm
        out["target"] = tgt
        out["note"] = "unlink submitted — confirm with kaseya_command_output"
    return out
