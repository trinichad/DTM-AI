"""Update an Autopilot device's group tag / assigned user / name (D-56; SOP: m365-graph)."""
from __future__ import annotations

from typing import Any

NAME = "m365_update_autopilot_device"
DESCRIPTION = ("Update an EXISTING Autopilot device: set/change its group tag, assigned user, "
               "or display name. Pass `serial` for one device or `serials` (a list) to update "
               "MANY in ONE call — do NOT call this tool once per device (the same group tag / "
               "assigned user / display name apply to each). Find devices by serial with "
               "m365_list_autopilot_devices. Intune applies the change asynchronously — re-check "
               "the list afterwards.")
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
                    "description": "update MANY devices in ONE call — a list of serial numbers; the "
                                   "same group tag / assigned user / display name apply to each, "
                                   "and results come back together. Use this instead of calling the "
                                   "tool once per device."},
        "group_tag": {"type": "string", "description": "new group tag (optional)"},
        "assigned_user": {"type": "string", "description": "UPN to assign (optional)"},
        "display_name": {"type": "string", "description": "new device name (optional)"},
    },
    "additionalProperties": False,
}


def run(ctx, serial: str = "", serials: Any = None, group_tag: str = "", assigned_user: str = "",
        display_name: str = "", **_: Any):
    wanted = [str(x).strip() for x in (serials or []) if str(x).strip()]
    if wanted:                                         # batch update (D-110) — one call, many devices
        results = [_one(ctx, x, group_tag, assigned_user, display_name) for x in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "updates_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, serial, group_tag, assigned_user, display_name)


def _one(ctx, serial: str, group_tag: str = "", assigned_user: str = "",
         display_name: str = "", **_: Any) -> dict:
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read, scoped_write
    from . import _graph_common as g
    serial = (serial or "").strip()
    user = (assigned_user or "").strip()
    if user and "@" not in user:
        return {"ok": False, "serial": serial, "error": f"'{user}' is not a sign-in address"}
    body: dict[str, Any] = {}
    if (group_tag or "").strip():
        body["groupTag"] = group_tag.strip()
    if user:
        body["userPrincipalName"] = user
    if (display_name or "").strip():
        body["displayName"] = display_name.strip()
    if not body:
        return {"ok": False, "serial": serial,
                "error": "give group_tag, assigned_user, and/or display_name"}

    try:
        dev, bad = g.find_autopilot_by_serial(ctx, serial)   # spaced-serial safe (D-67)
        if bad:
            return {**bad, "serial": serial}
        if not dev:
            return {"ok": False, "serial": serial,
                    "error": f"no Autopilot device with serial '{serial}' — "
                             f"check m365_list_autopilot_devices"}
        dev_id = str(dev.get("id"))
        r = scoped_write(
            ctx, "m365",
            f"/deviceManagement/windowsAutopilotDeviceIdentities/{dev_id}"
            f"/updateDeviceProperties",
            body=body, method="POST")
    except HttpError as exc:
        return {**g.err403(exc, "updating the device",
                           "DeviceManagementServiceConfig.ReadWrite.All"), "serial": serial}
    bad = g.fail(r)
    if bad:
        return {**bad, "serial": serial}
    return {"ok": True, "serial": serial, "updated": sorted(body),
            "note": "update SUBMITTED — Intune applies it asynchronously; confirm with "
                    "m365_list_autopilot_devices in a few minutes"}
