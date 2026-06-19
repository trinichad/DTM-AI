"""Add a DNS resource record (D-78; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_dns_add_record"
DESCRIPTION = ("Add a DNS record to a zone. Give the DNS `server`, the `zone` (e.g. acme.local), "
               "the record `type` (A, AAAA, CNAME, MX, TXT, PTR), the record `name` (e.g. 'www', "
               "or '@' for the zone root), and the `data` (an IP for A/AAAA, the target host for "
               "CNAME/MX/PTR, or the text for TXT). MX also needs `priority`. Optional `ttl_hours`. "
               "Remove it later with kaseya_dns_remove_record. Read the result with "
               "kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_dns"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_ZONE_RE = re.compile(r'^[A-Za-z0-9._-]{1,255}$')
_NAME_RE = re.compile(r'^[A-Za-z0-9._*@-]{1,255}$')
_HOST_RE = re.compile(r'^[A-Za-z0-9._-]{1,255}$')
_IPV6_RE = re.compile(r'^[0-9A-Fa-f:]{2,45}$')
_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "PTR")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the DNS server's machine name/AgentId"},
        "zone": {"type": "string", "description": "the zone name, e.g. acme.local"},
        "type": {"type": "string", "enum": list(_TYPES), "description": "the record type"},
        "name": {"type": "string", "description": "the record name (e.g. 'www', or '@' for root)"},
        "data": {"type": "string", "description": "IP (A/AAAA), target host (CNAME/MX/PTR), or text (TXT)"},
        "priority": {"type": "integer", "minimum": 0, "maximum": 65535,
                     "description": "MX preference (required for MX)"},
        "ttl_hours": {"type": "integer", "minimum": 0, "maximum": 168,
                      "description": "time-to-live in hours (optional)"},
    },
    "required": ["server", "zone", "type", "name", "data"],
    "additionalProperties": False,
}


def run(ctx, server: str, zone: str, type: str, name: str, data: str,
        priority: Any = None, ttl_hours: Any = None, **_: Any):
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
        return {"ok": False, "error": "give the record data"}

    # map type → cmdlet + the data parameter, validating the data shape
    if rr == "A":
        if not k.is_ipv4(dat):
            return {"ok": False, "error": "A record data must be an IPv4 address"}
        cmdlet, dparam = "Add-DnsServerResourceRecordA", "-IPv4Address " + k.ps_quote(dat)
    elif rr == "AAAA":
        if not _IPV6_RE.match(dat):
            return {"ok": False, "error": "AAAA record data must be an IPv6 address"}
        cmdlet, dparam = "Add-DnsServerResourceRecordAAAA", "-IPv6Address " + k.ps_quote(dat)
    elif rr == "CNAME":
        if not _HOST_RE.match(dat):
            return {"ok": False, "error": "CNAME data must be a host name"}
        cmdlet, dparam = "Add-DnsServerResourceRecordCName", "-HostNameAlias " + k.ps_quote(dat)
    elif rr == "PTR":
        if not _HOST_RE.match(dat):
            return {"ok": False, "error": "PTR data must be a host name"}
        cmdlet, dparam = "Add-DnsServerResourceRecordPtr", "-PtrDomainName " + k.ps_quote(dat)
    elif rr == "TXT":
        cmdlet, dparam = "Add-DnsServerResourceRecordTxt", "-DescriptiveText " + k.ps_quote(dat)
    else:  # MX
        if not _HOST_RE.match(dat):
            return {"ok": False, "error": "MX data must be a mail-exchange host name"}
        try:
            pref = int(priority)
        except (TypeError, ValueError):
            return {"ok": False, "error": "MX records need a priority (0-65535)"}
        if not 0 <= pref <= 65535:
            return {"ok": False, "error": "priority must be between 0 and 65535"}
        cmdlet = "Add-DnsServerResourceRecordMX"
        dparam = "-MailExchange " + k.ps_quote(dat) + " -Preference " + str(pref)

    parts = [cmdlet, "-ZoneName", k.ps_quote(z), "-Name", k.ps_quote(nm), dparam]
    if ttl_hours is not None:
        try:
            h = int(ttl_hours)
        except (TypeError, ValueError):
            return {"ok": False, "error": "ttl_hours must be a whole number of hours"}
        if not 0 <= h <= 168:
            return {"ok": False, "error": "ttl_hours must be between 0 and 168"}
        parts.append("-TimeToLive (New-TimeSpan -Hours " + str(h) + ")")

    cmd = ("try { Import-Module DnsServer; " + " ".join(parts) + "; 'OK: added " + rr +
           " record' } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out.update({"zone": z, "record_type": rr, "name": nm, "data": dat})
        out["note"] = "add submitted — confirm with kaseya_command_output"
    return out
