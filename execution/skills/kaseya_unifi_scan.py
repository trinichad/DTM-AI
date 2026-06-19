"""Scan a client LAN for Ubiquiti/UniFi devices via Kaseya (D-85; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_unifi_scan"
DESCRIPTION = ("Find Ubiquiti/UniFi devices on a client's local network — useful when new gear "
               "isn't showing up in the controller. Runs from a Windows machine ON that LAN "
               "(via Kaseya): it ping-sweeps the machine's /24, then matches the ARP table against "
               "Ubiquiti MAC prefixes and reports each device's IP + MAC. Give the `machine` (a "
               "Kaseya agent on the client network). Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_unifi"
CATEGORY = "write"            # runs a command on the endpoint (gated like all command tools)
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "a Kaseya agent ON the client's LAN (name/AgentId)"},
    },
    "required": ["machine"],
    "additionalProperties": False,
}

# Ubiquiti OUI prefixes (first 3 MAC octets, lowercase no separators). Representative, not exhaustive.
_OUIS = ("00156d", "002722", "006967", "0418d6", "18e829", "245a4c", "24a43c", "287be0", "28704e",
         "44d9e7", "60223e", "687251", "70a741", "742344", "7483c2", "788a20", "784558", "802aa8",
         "942a6f", "9c05d6", "ac8ba9", "b4fbe4", "d021f9", "dc9fdb", "e063da", "e43883", "f09fc2",
         "f492bf", "fcecda", "68d79a", "74acb9")
_PS = r"""try {
  $local = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notmatch '^(169\.254|127\.)' } | Select-Object -First 1 -ExpandProperty IPAddress)
  if (-not $local) { throw 'no usable local IPv4 address on this machine' }
  $prefix = ($local -split '\.')[0..2] -join '.'
  $tasks = 1..254 | ForEach-Object { (New-Object System.Net.NetworkInformation.Ping).SendPingAsync("$prefix.$_", 300) }
  [System.Threading.Tasks.Task]::WaitAll($tasks) | Out-Null
  Start-Sleep -Milliseconds 400
  $ouis = @(@@OUIS@@)
  $rows = @()
  foreach ($line in (arp -a)) {
    if ($line -match '(\d{1,3}(\.\d{1,3}){3})\s+([0-9a-fA-F]{2}([:-][0-9a-fA-F]{2}){5})') {
      $ip = $matches[1]; $mac = ($matches[3] -replace '[:-]','').ToLower()
      if ($ouis -contains $mac.Substring(0,6)) {
        $rows += [PSCustomObject]@{ IP=$ip; MAC=(($mac -replace '(.{2})','$1:').TrimEnd(':')) }
      }
    }
  }
  if ($rows) { "Ubiquiti devices on $prefix.0/24:`n" + ($rows | Sort-Object IP | Format-Table -AutoSize | Out-String) }
  else { "No Ubiquiti devices found on $prefix.0/24 (ARP/OUI scan). The device may be on another subnet/VLAN, powered off, or have a newer MAC prefix — you can still target it by IP with kaseya_unifi_set_inform." }
} catch { 'ERROR: ' + $_.Exception.Message }"""


def run(ctx, machine: str, **_: Any):
    from . import _kaseya_common as k
    cmd = _PS.replace("@@OUIS@@", ",".join("'" + o + "'" for o in _OUIS))
    out = k.run_command(ctx, machine, cmd)
    if out.get("ok"):
        out["note"] = "scan submitted — read the device list with kaseya_command_output"
    return out
