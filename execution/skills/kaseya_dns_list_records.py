"""List DNS records in a zone (D-78; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_dns_list_records"
DESCRIPTION = ("List the resource records in a DNS zone: name, type, TTL, and data. Optionally "
               "filter by record type (A, AAAA, CNAME, MX, TXT, PTR, NS, SRV) and/or name. "
               "Read-only, but rides the command engine so it's approval-gated. Give the DNS "
               "`server` and the `zone`. Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_dns"
CATEGORY = "write"            # read-only in effect; runs a command on the endpoint
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_ZONE_RE = re.compile(r'^[A-Za-z0-9._-]{1,255}$')
_NAME_RE = re.compile(r'^[A-Za-z0-9._*@-]{1,255}$')
_RRTYPES = {"a": "A", "aaaa": "AAAA", "cname": "CNAME", "mx": "MX", "txt": "TXT",
            "ptr": "PTR", "ns": "NS", "srv": "SRV"}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the DNS server's machine name/AgentId"},
        "zone": {"type": "string", "description": "the zone name, e.g. acme.local"},
        "type": {"type": "string", "enum": list(_RRTYPES), "description": "filter by record type (optional)"},
        "name": {"type": "string", "description": "filter by record name (optional), e.g. www"},
    },
    "required": ["server", "zone"],
    "additionalProperties": False,
}


def run(ctx, server: str, zone: str, type: str = "", name: str = "", **_: Any):
    from . import _kaseya_common as k
    z = str(zone or "").strip()
    if not _ZONE_RE.match(z):
        return {"ok": False, "error": "give a valid zone name, e.g. acme.local"}
    parts = ["Get-DnsServerResourceRecord", "-ZoneName", k.ps_quote(z)]
    if (type or "").strip():
        rr = _RRTYPES.get(type.strip().lower())
        if not rr:
            return {"ok": False, "error": "type must be one of: " + ", ".join(_RRTYPES)}
        parts += ["-RRType", rr]
    if (name or "").strip():
        nm = str(name).strip()
        if not _NAME_RE.match(nm):
            return {"ok": False, "error": "the name filter has invalid characters"}
        parts += ["-Name", k.ps_quote(nm)]
    cmd = ("try { Import-Module DnsServer; " + " ".join(parts) +
           " | Select-Object HostName,RecordType,@{N='TTL';E={$_.TimeToLive}},"
           "@{N='Data';E={($_.RecordData | Format-List | Out-String).Trim()}} | "
           "Format-Table -AutoSize | Out-String -Width 4096 } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["zone"] = z
        out["note"] = "read-only — read the record list with kaseya_command_output"
    return out
