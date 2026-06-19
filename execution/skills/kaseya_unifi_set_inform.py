"""SSH set-inform a UniFi device to a controller via Kaseya (D-85; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_unifi_set_inform"
DESCRIPTION = ("Point a UniFi device at its controller by SSHing in and running `set-inform` — the "
               "fix when a device won't appear for adoption. Runs from a Windows machine ON the "
               "client's LAN (via Kaseya, using plink). Give the `machine` (Kaseya agent on that "
               "LAN), the device's `device_ip`, and the `controller` host/IP the device should "
               "inform (e.g. the on-site controller's LAN IP). Optional port (default 8080), "
               "ssh_user/ssh_pass (default ubnt/ubnt — factory devices; for an adopted device use "
               "the controller's configured SSH credentials). After it runs, adopt the device in "
               "the controller, then run this again. Read the result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_unifi"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "a Kaseya agent ON the client's LAN (name/AgentId)"},
        "device_ip": {"type": "string", "description": "the UniFi device's IP on the LAN"},
        "controller": {"type": "string", "description": "the controller host/IP the device should inform"},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535,
                 "description": "the controller inform port (default 8080)"},
        "ssh_user": {"type": "string", "description": "device SSH user (default ubnt)"},
        "ssh_pass": {"type": "string", "description": "device SSH password (default ubnt)"},
    },
    "required": ["machine", "device_ip", "controller"],
    "additionalProperties": False,
}


def run(ctx, machine: str, device_ip: str, controller: str, port: Any = None,
        ssh_user: str = "ubnt", ssh_pass: str = "ubnt", **_: Any):
    from . import _kaseya_common as k
    if not k.is_ipv4(device_ip):
        return {"ok": False, "error": "device_ip must be a valid IPv4 address"}
    host = (controller or "").strip()
    if not (_HOST_RE.match(host) or k.is_ipv4(host)):
        return {"ok": False, "error": "controller must be a hostname or IP"}
    user = (ssh_user or "ubnt").strip()
    if not _USER_RE.match(user):
        return {"ok": False, "error": "ssh_user has invalid characters"}
    pw = k.clean_text(ssh_pass or "ubnt", 128)
    if not pw:
        return {"ok": False, "error": "give a valid ssh_pass"}
    p = 8080
    if port is not None:
        p = int(port)
        if not 1 <= p <= 65535:
            return {"ok": False, "error": "port must be between 1 and 65535"}
    inform = f"http://{host}:{p}/inform"
    cmd = k.ssh_command_ps(user, device_ip.strip(), pw, f"set-inform {inform}")
    out = k.run_command(ctx, machine, cmd)
    if out.get("ok"):
        out.update({"device_ip": device_ip.strip(), "inform_url": inform})
        out["note"] = ("set-inform submitted — the device should appear as 'Pending Adoption'; "
                       "adopt it in the controller, then run this again. The SSH password is part "
                       "of the command (visible on the approval card + Kaseya log).")
    return out
