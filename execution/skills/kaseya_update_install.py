"""Install pending Windows Updates on a machine (D-81; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_update_install"
DESCRIPTION = ("Download and install all pending Windows Updates on a machine (built-in Windows "
               "Update API — no extra module). Can take a long time; reports how many installed "
               "and whether a reboot is required (it does NOT auto-reboot — use "
               "kaseya_reboot_machine). Give the `server`. Read the result with "
               "kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_update"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine (name/AgentId)"},
    },
    "required": ["server"],
    "additionalProperties": False,
}

_PS = r"""try {
  $session = New-Object -ComObject Microsoft.Update.Session
  $found = $session.CreateUpdateSearcher().Search("IsInstalled=0 AND IsHidden=0").Updates
  if (@($found).Count -eq 0) { 'No pending updates.' }
  else {
    $coll = New-Object -ComObject Microsoft.Update.UpdateColl
    foreach ($u in $found) { if (-not $u.EulaAccepted) { try { $u.AcceptEula() } catch {} }; $coll.Add($u) | Out-Null }
    $dl = $session.CreateUpdateDownloader(); $dl.Updates = $coll; $dl.Download() | Out-Null
    $inst = $session.CreateUpdateInstaller(); $inst.Updates = $coll
    $res = $inst.Install()
    "Installed $(@($coll).Count) update(s). ResultCode=$($res.ResultCode) (2=Succeeded). RebootRequired=$($res.RebootRequired)"
  }
} catch { 'ERROR: ' + $_.Exception.Message }"""


def run(ctx, server: str, **_: Any):
    from . import _kaseya_common as k
    out = k.run_command(ctx, server, _PS)
    if out.get("ok"):
        out["note"] = ("install submitted — runs for a while; read the result with "
                       "kaseya_command_output. If RebootRequired=True, use kaseya_reboot_machine.")
    return out
