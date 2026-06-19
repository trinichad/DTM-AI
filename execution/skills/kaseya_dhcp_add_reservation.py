"""Add a DHCP reservation — pin an IP to a MAC (D-76; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_dhcp_add_reservation"
DESCRIPTION = ("Reserve a DHCP IP for a device (pin an IP to a MAC) in a scope. Give the `server`, "
               "the `scope_id` (e.g. 192.168.1.0), the `ip` to reserve, and the device `mac` "
               "(any common format). Optional: name and description. Remove it later with "
               "kaseya_dhcp_remove_reservation. Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_dhcp"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the DHCP server's machine name/AgentId"},
        "scope_id": {"type": "string", "description": "the scope's network address, e.g. 192.168.1.0"},
        "ip": {"type": "string", "description": "the IP address to reserve"},
        "mac": {"type": "string", "description": "the device MAC / ClientId (any common format)"},
        "name": {"type": "string", "description": "reservation name (optional)"},
        "description": {"type": "string", "description": "reservation description (optional)"},
    },
    "required": ["server", "scope_id", "ip", "mac"],
    "additionalProperties": False,
}


def run(ctx, server: str, scope_id: str, ip: str, mac: str, name: str = "",
        description: str = "", **_: Any):
    from . import _kaseya_common as k
    sid = str(scope_id or "").strip()
    if not k.is_ipv4(sid):
        return {"ok": False, "error": "scope_id must be the scope's network address, e.g. 192.168.1.0"}
    if not k.is_ipv4(ip):
        return {"ok": False, "error": "ip must be a valid IPv4 address"}
    cid = k.clean_mac(mac)
    if not cid:
        return {"ok": False, "error": "mac must be a valid MAC address (12 hex digits)"}

    parts = ["Add-DhcpServerv4Reservation", "-ScopeId", k.ps_quote(sid),
             "-IPAddress", k.ps_quote(ip.strip()), "-ClientId", k.ps_quote(cid)]
    if (name or "").strip():
        nm = k.clean_text(name, 256)
        if not nm:
            return {"ok": False, "error": "the name is not valid"}
        parts += ["-Name", k.ps_quote(nm)]
    if (description or "").strip():
        d = k.clean_text(description, 1024)
        if not d:
            return {"ok": False, "error": "the description is not valid"}
        parts += ["-Description", k.ps_quote(d)]

    cmd = ("try { Import-Module DhcpServer; " + " ".join(parts) + "; 'OK: reserved " +
           ip.strip() + " for " + cid + "' } catch { 'ERROR: ' + $_.Exception.Message }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out["scope_id"] = sid
        out["ip"] = ip.strip()
        out["mac"] = cid
        out["note"] = "add submitted — confirm with kaseya_command_output"
    return out
