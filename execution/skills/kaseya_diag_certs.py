"""Certificates nearing expiry on a machine (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_diag_certs"
DESCRIPTION = ("List certificates in the machine's store that are expired or expiring soon — "
               "catches the silent 'cert expired over the weekend' outage. Read-only. Give the "
               "`server`; optional `days` window (default 60). Read the result with "
               "kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_diag"
CATEGORY = "write"            # read-only in effect; runs a command on the endpoint
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine to inspect (name/AgentId)"},
        "days": {"type": "integer", "minimum": 1, "maximum": 3650,
                 "description": "flag certs expiring within this many days (default 60)"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, days: Any = None, **_: Any):
    from . import _kaseya_common as k
    d = 60
    if days is not None:
        try:
            d = int(days)
        except (TypeError, ValueError):
            return {"ok": False, "error": "days must be a whole number"}
        if not 1 <= d <= 3650:
            return {"ok": False, "error": "days must be between 1 and 3650"}
    cmd = ("try { $cut = (Get-Date).AddDays(" + str(d) + "); "
           "Get-ChildItem Cert:\\LocalMachine\\My -Recurse -ErrorAction SilentlyContinue | "
           "Where-Object { $_.NotAfter -and ($_.NotAfter -lt $cut) } | "
           "Select-Object Subject,NotAfter,@{N='DaysLeft';E={[math]::Round(($_.NotAfter - "
           "(Get-Date)).TotalDays)}},Thumbprint | Sort-Object NotAfter | Format-Table -AutoSize | "
           "Out-String -Width 4096 } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["within_days"] = d
        out["note"] = "read-only — read the cert report with kaseya_command_output"
    return out
