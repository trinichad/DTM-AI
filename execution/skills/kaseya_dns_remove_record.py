"""Remove a DNS resource record (D-78; SOP: kaseya-vsa). Opposite of kaseya_dns_add_record."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_dns_remove_record"
DESCRIPTION = ("Remove a DNS record from a zone. Give the DNS `server`, the `zone`, the record "
               "`type` (A, AAAA, CNAME, MX, TXT, PTR), the record `name`, and the `data` "
               "identifying the exact record to delete (the IP, target host, or text). Read the "
               "result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_dns"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_ZONE_RE = re.compile(r'^[A-Za-z0-9._-]{1,255}$')
_NAME_RE = re.compile(r'^[A-Za-z0-9._*@-]{1,255}$')
_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "PTR")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the DNS server's machine name/AgentId"},
        "zone": {"type": "string", "description": "the zone name, e.g. acme.local"},
        "type": {"type": "string", "enum": list(_TYPES), "description": "the record type"},
        "name": {"type": "string", "description": "the record name (e.g. 'www', or '@' for root)"},
        "data": {"type": "string", "description": "the record data identifying which one to remove"},
    },
    "required": ["server", "zone", "type", "name", "data"],
    "additionalProperties": False,
}


def run(ctx, server: str, zone: str, type: str, name: str, data: str, **_: Any):
    from . import _kaseya_common as k
    z = str(zone or "").strip()
    if not _ZONE_RE.match(z):
        return {"ok": False, "error": "give a valid zone name, e.g. acme.local"}
    rr = str(type or "").strip().upper()
    if rr not in _TYPES:
        return {"ok": False, "error": "type must be one of: " + ", ".join(_TYPES)}
    nm = str(name or "").strip()
    if not _NAME_RE.match(nm):
        return {"ok": False, "error": "the record name has invalid characters"}
    dat = k.clean_text(data, 512)
    if not dat:
        return {"ok": False, "error": "give the record data identifying which record to remove"}

    cmd = ("try { Import-Module DnsServer; Remove-DnsServerResourceRecord -ZoneName " +
           k.ps_quote(z) + " -Name " + k.ps_quote(nm) + " -RRType " + rr + " -RecordData " +
           k.ps_quote(dat) + " -Force -Confirm:$false; 'OK: removed " + rr + " record' } "
           "catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out.update({"zone": z, "record_type": rr, "name": nm, "data": dat})
        out["note"] = "remove submitted — confirm with kaseya_command_output"
    return out
