"""Remove a Windows Autopilot device (D-65; SOP: m365-graph).
The opposite of m365_add_autopilot_device."""
from __future__ import annotations

from typing import Any

NAME = "m365_remove_autopilot_device"
DESCRIPTION = ("Remove a device from Windows AUTOPILOT by serial number (deregisters its "
               "hardware hash). The device itself is untouched; it just won't Autopilot-enroll "
               "anymore. Pass `serial` for one device or `serials` (a list) to remove MANY in "
               "ONE call — do NOT call this tool once per device. Verifies removal before "
               "reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "serial": {"type": "string", "description": "the device's serial number"},
        "serials": {"type": "array", "items": {"type": "string"},
                    "description": "remove MANY devices in ONE call — a list of serial numbers; "
                                   "each removal is verified and results come back together. Use "
                                   "this instead of calling the tool once per device."},
    },
    "additionalProperties": False,
}

_BASE = "/deviceManagement/windowsAutopilotDeviceIdentities"


def run(ctx, serial: str = "", serials: Any = None, **_: Any):
    wanted = [str(x).strip() for x in (serials or []) if str(x).strip()]
    if wanted:                                         # batch remove (D-110) — one call, many devices
        results = ctx.map_progress(wanted[:500], lambda x: _one(ctx, x))
        return {"ok": any(r.get("ok") for r in results), "removals_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, serial)


def _one(ctx, serial: str) -> dict:
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_delete, scoped_read
    from . import _graph_common as g
    serial = (serial or "").strip()
    if not serial:
        return {"ok": False, "error": "a serial number is required"}
    try:
        dev, bad = g.find_autopilot_by_serial(ctx, serial)   # spaced-serial safe (D-67)
        if bad:
            return {**bad, "serial": serial}
        if not dev:
            return {"ok": True, "serial": serial,
                    "note": "no Autopilot device with that serial — nothing to remove"}
        r = scoped_delete(ctx, "m365", f"{_BASE}/{dev.get('id')}")
        bad = g.fail(r)
        if bad:
            return {**bad, "serial": serial}
        check, _ = g.find_autopilot_by_serial(ctx, serial)
    except HttpError as exc:
        return {**g.err403(exc, "removing the device",
                           "DeviceManagementServiceConfig.ReadWrite.All"), "serial": serial}
    if check:
        return {"ok": False, "serial": serial, "step": "verify",
                "error": "the device is still registered after removal (Intune can lag a few "
                         "minutes) — re-check with m365_list_autopilot_devices"}
    return {"ok": True, "serial_removed": serial}
