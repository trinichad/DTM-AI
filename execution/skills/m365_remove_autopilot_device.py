"""Remove a Windows Autopilot device (D-65; SOP: m365-graph).
The opposite of m365_add_autopilot_device."""
from __future__ import annotations

from typing import Any

NAME = "m365_remove_autopilot_device"
DESCRIPTION = ("Remove a device from Windows AUTOPILOT by serial number (deregisters its "
               "hardware hash). The device itself is untouched; it just won't Autopilot-enroll "
               "anymore. Verifies removal before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "serial": {"type": "string", "description": "the device's serial number"},
    },
    "required": ["serial"],
    "additionalProperties": False,
}

_BASE = "/deviceManagement/windowsAutopilotDeviceIdentities"


def run(ctx, serial: str, **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_delete, scoped_read
    from . import _graph_common as g
    serial = (serial or "").strip()
    if not serial:
        return {"ok": False, "error": "a serial number is required"}
    try:
        dev, bad = g.find_autopilot_by_serial(ctx, serial)   # spaced-serial safe (D-67)
        if bad:
            return bad
        if not dev:
            return {"ok": True, "serial": serial,
                    "note": "no Autopilot device with that serial — nothing to remove"}
        r = scoped_delete(ctx, "m365", f"{_BASE}/{dev.get('id')}")
        bad = g.fail(r)
        if bad:
            return bad
        check, _ = g.find_autopilot_by_serial(ctx, serial)
    except HttpError as exc:
        return g.err403(exc, "removing the device",
                        "DeviceManagementServiceConfig.ReadWrite.All")
    if check:
        return {"ok": False, "step": "verify",
                "error": "the device is still registered after removal (Intune can lag a few "
                         "minutes) — re-check with m365_list_autopilot_devices"}
    return {"ok": True, "serial_removed": serial}
