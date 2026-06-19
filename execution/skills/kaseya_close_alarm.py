"""Close a Kaseya alarm with a note (D-69; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_close_alarm"
DESCRIPTION = ("Close a Kaseya ALARM (the only state change Kaseya exposes — there's no separate "
               "acknowledge). Pass the AlarmId and a short reason/note. Find alarm ids with "
               "kaseya_list_alarms.")
SOURCE = "kaseya"
CATEGORY = "write"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "alarm_id": {"type": "string", "description": "the AlarmId to close"},
        "reason": {"type": "string", "description": "note explaining why it's being closed"},
    },
    "required": ["alarm_id", "reason"],
    "additionalProperties": False,
}


def run(ctx, alarm_id: str, reason: str, **_: Any):
    alarm_id = str(alarm_id or "").strip()
    if not alarm_id.isdigit():
        return {"ok": False, "error": "alarm_id must be the numeric AlarmId"}
    r = ctx.client("kaseya").write("PUT", f"/assetmgmt/alarms/{alarm_id}/close",
                                   {"key": "notes", "value": (reason or "").strip()})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "closed_alarm": alarm_id, "note": (reason or "").strip()}
