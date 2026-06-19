"""SSH factory-reset a UniFi device via Kaseya (D-85; SOP: kaseya-vsa) — DESTRUCTIVE."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_unifi_factory_reset"
DESCRIPTION = ("Factory-reset a UniFi device by SSHing in and running `set-default` — for when a "
               "device is stuck/unreachable from the controller and needs to start clean. Runs "
               "from a Windows machine ON the client's LAN (via Kaseya, using plink). Give the "
               "`machine` (Kaseya agent on that LAN) and the device's `device_ip`. Optional "
               "ssh_user/ssh_pass (default ubnt/ubnt). DESTRUCTIVE — wipes the device's config and "
               "reboots it; it will need re-adopting. Always needs a per-action approval. Read the "
               "result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_unifi"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "a Kaseya agent ON the client's LAN (name/AgentId)"},
        "device_ip": {"type": "string", "description": "the UniFi device's IP on the LAN"},
        "ssh_user": {"type": "string", "description": "device SSH user (default ubnt)"},
        "ssh_pass": {"type": "string", "description": "device SSH password (default ubnt)"},
    },
    "required": ["machine", "device_ip"],
    "additionalProperties": False,
}


def run(ctx, machine: str, device_ip: str, ssh_user: str = "ubnt", ssh_pass: str = "ubnt", **_: Any):
    from . import _kaseya_common as k
    if not k.is_ipv4(device_ip):
        return {"ok": False, "error": "device_ip must be a valid IPv4 address"}
    user = (ssh_user or "ubnt").strip()
    if not _USER_RE.match(user):
        return {"ok": False, "error": "ssh_user has invalid characters"}
    pw = k.clean_text(ssh_pass or "ubnt", 128)
    if not pw:
        return {"ok": False, "error": "give a valid ssh_pass"}
    cmd = k.ssh_command_ps(user, device_ip.strip(), pw, "set-default")
    out = k.run_command(ctx, machine, cmd)
    if out.get("ok"):
        out["device_ip"] = device_ip.strip()
        out["note"] = ("factory-reset submitted — the device wipes its config and reboots (the SSH "
                       "session drops, which is expected). Re-adopt it afterward. The password is "
                       "visible on the approval card + Kaseya log.")
    return out
