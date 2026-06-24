"""List Windows Autopilot devices in Intune (D-56; SOP: m365-graph)."""
from __future__ import annotations

from typing import Any

NAME = "m365_list_autopilot_devices"
DESCRIPTION = ("List the client's Windows AUTOPILOT devices in Intune: serial number, "
               "manufacturer/model, group tag, assigned user, enrollment state. Use `serial` "
               "to find one device, or `serials` (a list) to look up MANY in ONE call — do NOT "
               "call this tool once per device. Also how you confirm an import finished "
               "(m365_add_autopilot_device).")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "serial": {"type": "string", "description": "find by serial number (optional)"},
        "serials": {"type": "array", "items": {"type": "string"},
                    "description": "look up MANY devices in ONE call — a list of serial numbers; "
                                   "each device's details come back together. Use this instead of "
                                   "calling the tool once per device."},
        "limit": {"type": "integer", "description": "max results (default 100, max 500)"},
    },
    "additionalProperties": False,
}


def run(ctx, serial: str = "", serials: Any = None, limit: int = 100, **_: Any):
    limit = max(1, min(int(limit or 100), 500))
    wanted = [str(x).strip() for x in (serials or []) if str(x).strip()]
    if wanted:                                         # batch lookup (D-110) — one call, many devices
        results = [_one(ctx, x, limit) for x in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "devices_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, serial, limit)


def _one(ctx, serial: str, limit: int) -> dict:
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read
    from . import _graph_common as g
    q = (serial or "").strip()
    # A serial with a space/hyphen can 400 the contains() filter (D-67) — use the server filter
    # only for clean serials; otherwise fetch a page and substring-match client-side.
    use_filter = bool(q) and " " not in q and "'" not in q
    base = "/deviceManagement/windowsAutopilotDeviceIdentities"
    params: dict[str, Any] = {"$top": limit}
    if use_filter:
        params["$filter"] = f"contains(serialNumber,'{q}')"
    try:
        data = scoped_read(ctx, "m365", base, params)
        if use_filter and g.fail(data):                  # filter rejected → unfiltered scan
            data = scoped_read(ctx, "m365", base, {"$top": limit})
    except HttpError as exc:
        bad = g.err403(exc, "listing Autopilot devices",
                       "DeviceManagementServiceConfig.ReadWrite.All")
        return {**bad, **({"searched_for": q} if q else {})}
    bad = g.fail(data)
    if bad:
        return {**bad, **({"searched_for": q} if q else {})}
    rows = g.rows(data)
    if q and not use_filter:                             # client-side substring match
        rows = [d for d in rows if q.lower() in str(d.get("serialNumber") or "").lower()]
    devices = [{"serial": d.get("serialNumber"), "id": d.get("id"),
                "manufacturer": d.get("manufacturer"), "model": d.get("model"),
                "group_tag": d.get("groupTag"),
                "assigned_user": d.get("userPrincipalName"),
                "enrollment_state": d.get("enrollmentState"),
                "last_contacted": d.get("lastContactedDateTime")}
               for d in rows]
    out: dict[str, Any] = {"ok": True, "count": len(devices), "devices": devices}
    if q:
        out["searched_for"] = q
        if not devices:
            out["note"] = (f"no Autopilot device with serial matching '{q}' — a recent "
                           f"import can take ~15 min to appear")
    return out
