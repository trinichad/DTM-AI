"""Clear the DNS server cache (D-78; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_dns_clear_cache"
DESCRIPTION = ("Clear the resolver cache on a Windows DNS server (Clear-DnsServerCache) — useful "
               "after a record change so stale answers stop being served. Give the DNS server's "
               "`server`. Read the result with kaseya_command_output. (To flush a CLIENT "
               "machine's cache instead, use kaseya_flush_dns.)")
SOURCE = "kaseya"
GROUP = "kaseya_dns"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the DNS server's machine name/AgentId"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, **_: Any):
    from . import _kaseya_common as k
    cmd = ("try { Import-Module DnsServer; Clear-DnsServerCache -Force -Confirm:$false; "
           "'OK: DNS server cache cleared' } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["note"] = "clear submitted — confirm with kaseya_command_output"
    return out
