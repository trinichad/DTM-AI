"""Import a device hardware hash into Windows Autopilot (D-56; SOP: m365-graph).

HONESTY RULE: the import is processed ASYNCHRONOUSLY by Intune — this tool reports the
import as SUBMITTED (with whatever status Intune returned), never as completed. Confirm
with m365_list_autopilot_devices after ~15 minutes.
"""
from __future__ import annotations

import base64
from typing import Any

NAME = "m365_add_autopilot_device"
DESCRIPTION = ("Import a device into Windows AUTOPILOT by hardware hash: serial number + the "
               "base64 hardware hash (from Get-WindowsAutopilotInfo / the OEM CSV), with an "
               "optional group tag and assigned user. The import is processed by Intune "
               "asynchronously (~15 min) — confirm afterwards with "
               "m365_list_autopilot_devices.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "serial": {"type": "string", "description": "the device's serial number"},
        "hardware_hash": {"type": "string",
                          "description": "the base64 hardware hash (long string from the "
                                         "CSV's 'Hardware Hash' column)"},
        "group_tag": {"type": "string", "description": "Autopilot group tag (optional)"},
        "assigned_user": {"type": "string",
                          "description": "UPN to pre-assign to the device (optional)"},
    },
    "required": ["serial", "hardware_hash"],
    "additionalProperties": False,
}


def run(ctx, serial: str, hardware_hash: str, group_tag: str = "", assigned_user: str = "",
        **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_write
    from . import _graph_common as g
    serial = (serial or "").strip()
    hh = "".join((hardware_hash or "").split())       # CSVs often wrap the hash
    if not serial:
        return {"ok": False, "error": "the device needs a serial number"}
    if len(hh) < 100:
        return {"ok": False, "error": "that doesn't look like a hardware hash — it's a long "
                                      "(1000+ char) base64 string from the Autopilot CSV"}
    try:
        base64.b64decode(hh, validate=True)
    except Exception:                                  # noqa: BLE001
        return {"ok": False, "error": "the hardware hash is not valid base64 — copy the "
                                      "'Hardware Hash' column value exactly"}
    user = (assigned_user or "").strip()
    if user and "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a sign-in address"}

    body: dict[str, Any] = {
        "@odata.type": "#microsoft.graph.importedWindowsAutopilotDeviceIdentity",
        "serialNumber": serial,
        "hardwareIdentifier": hh,
        "groupTag": (group_tag or "").strip(),
        "assignedUserPrincipalName": user,
    }
    try:
        r = scoped_write(ctx, "m365",
                         "/deviceManagement/importedWindowsAutopilotDeviceIdentities",
                         body=body, method="POST")
    except HttpError as exc:
        return g.err403(exc, "importing the device",
                        "DeviceManagementServiceConfig.ReadWrite.All")
    bad = g.fail(r)
    if bad:
        return bad
    state = (r.get("state") or {}) if isinstance(r, dict) else {}
    status = str(state.get("deviceImportStatus") or "pending")
    if status.lower() in ("error", "failed"):
        return {"ok": False, "serial": serial, "import_status": status,
                "error": str(state.get("deviceErrorName") or "Intune rejected the import")}
    return {"ok": True, "serial": serial, "import_status": status,
            **({"group_tag": group_tag.strip()} if (group_tag or "").strip() else {}),
            **({"assigned_user": user} if user else {}),
            "note": "import SUBMITTED — Intune processes it asynchronously (~15 min); "
                    "confirm with m365_list_autopilot_devices serial=" + serial}
