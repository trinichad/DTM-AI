"""Resolve a DNS name from a machine — nslookup/Resolve-DnsName (D-78; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_dns_resolve"
DESCRIPTION = ("Look up a DNS name FROM a machine (what that machine's DNS actually returns) — "
               "great for troubleshooting 'is this resolving?'. Give the `server` (the machine to "
               "run the lookup from), the `name` to resolve, and optionally a record `type` "
               "(default A). Read-only. Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_dns"
CATEGORY = "write"            # read-only in effect; runs a command on the endpoint
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_NAME_RE = re.compile(r'^[A-Za-z0-9._-]{1,255}$')
_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "PTR", "NS", "SRV", "SOA")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine to run the lookup FROM (name/AgentId)"},
        "name": {"type": "string", "description": "the DNS name (or IP for PTR) to resolve"},
        "type": {"type": "string", "enum": list(_TYPES), "description": "record type (default A)"},
    },
    "required": ["server", "name"],
    "additionalProperties": False,
}


def run(ctx, server: str, name: str, type: str = "A", **_: Any):
    from . import _kaseya_common as k
    nm = str(name or "").strip()
    if not _NAME_RE.match(nm):
        return {"ok": False, "error": "give a valid DNS name (or IP for PTR)"}
    rr = str(type or "A").strip().upper()
    if rr not in _TYPES:
        return {"ok": False, "error": "type must be one of: " + ", ".join(_TYPES)}
    cmd = ("try { Resolve-DnsName -Name " + k.ps_quote(nm) + " -Type " + rr +
           " -ErrorAction Stop | Format-Table -AutoSize | Out-String -Width 4096 } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out.update({"name": nm, "record_type": rr})
        out["note"] = "read-only — read the lookup result with kaseya_command_output"
    return out
