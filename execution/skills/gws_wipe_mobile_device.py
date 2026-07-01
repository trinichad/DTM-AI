"""Wipe / block a managed mobile device — Admin SDK Directory API device action (D-118).

Default is the safest action: remove the corporate Google account + its data (account wipe), NOT a
full factory reset of the personal device. Get `resource_id` from gws_list_mobile_devices.
"""
from __future__ import annotations

from typing import Any

NAME = "gws_wipe_mobile_device"
DESCRIPTION = ("Act on a managed mobile device (offboarding / lost or stolen). `action`: "
               "account_wipe (default — remove the Google account + its work data only, leaves "
               "personal data), full_wipe (factory-reset the whole device), or block (block it from "
               "syncing). Pass `resource_id` from gws_list_mobile_devices. Irreversible on the "
               "device — requires approval.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

# friendly name -> Directory API action verb
_ACTIONS = {"account_wipe": "admin_account_wipe", "full_wipe": "admin_remote_wipe", "block": "block"}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "resource_id": {"type": "string",
                        "description": "the device's resourceId (from gws_list_mobile_devices)"},
        "action": {"type": "string", "enum": list(_ACTIONS),
                   "description": "account_wipe (default), full_wipe, or block"},
    },
    "required": ["resource_id"],
    "additionalProperties": False,
}


def run(ctx, resource_id: str = "", action: str = "account_wipe", **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_write
    from ._gws_write import err_msg, api_error
    rid = (resource_id or "").strip()
    if not rid:
        return {"ok": False, "error": "resource_id is required (from gws_list_mobile_devices)"}
    act = action if action in _ACTIONS else "account_wipe"
    verb = _ACTIONS[act]
    path = f"/admin/directory/v1/customer/my_customer/devices/mobile/{rid}/action"
    try:
        res = scoped_write(ctx, "gws", path, body={"action": verb}, method="POST")
    except HttpError as e:
        if getattr(e, "status", None) == 404:
            return {"ok": False, "error": f"mobile device '{rid}' not found"}
        return {"ok": False, "error": err_msg(e)}
    blocked = api_error(res)
    if blocked:
        return {"ok": False, "error": blocked}
    return {"ok": True, "resource_id": rid, "action": act,
            "note": f"'{act}' requested on the device (applies at its next sync)"}
