"""Trigger an Entra (Azure AD Connect) delta sync (D-72; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_entra_delta_sync"
DESCRIPTION = ("Trigger an Entra / Azure AD Connect SYNC on the sync server, so recent AD "
               "changes (new users, attribute/proxyAddress edits) push up to Microsoft 365 / "
               "Entra without waiting for the scheduled cycle. type 'delta' (default — just the "
               "changes) or 'initial' (full resync, slower). Pass the AAD Connect server's "
               "Kaseya machine name as `server`. Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_ad"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_TYPES = ("delta", "initial")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string",
                   "description": "the Azure AD Connect / sync server's machine name or AgentId"},
        "type": {"type": "string", "enum": list(_TYPES),
                 "description": "'delta' (default, just changes) or 'initial' (full resync)"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, type: str = "delta", **_: Any):
    from . import _kaseya_common as k
    stype = (type or "delta").strip().lower()
    if stype not in _TYPES:
        return {"ok": False, "error": f"type must be one of: {', '.join(_TYPES)}"}
    policy = "Delta" if stype == "delta" else "Initial"
    cmd = ("try { Import-Module ADSync; (Start-ADSyncSyncCycle -PolicyType " + policy +
           ") | Select-Object Result | Format-List | Out-String } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["sync_type"] = stype
        out["note"] = ("sync submitted on the AAD Connect server — confirm with "
                       "kaseya_command_output (a 'Success' Result means a cycle started; if it "
                       "says a sync is already running, wait and retry)")
    return out
