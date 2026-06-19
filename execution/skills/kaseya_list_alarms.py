"""List open Kaseya alarms (D-68; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_list_alarms"
DESCRIPTION = ("List Kaseya ALARMS — what's alarming across the client's machines right now "
               "(monitor/alert conditions that fired). Open alarms by default. Optionally filter "
               "by machine name substring. Use for 'what's red in Kaseya', 'any open alarms'.")
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine_contains": {"type": "string",
                             "description": "only alarms whose machine name contains this "
                                            "(optional)"},
        "include_closed": {"type": "boolean",
                           "description": "include closed alarms too (default false = open only)"},
        "limit": {"type": "integer", "description": "max alarms (default 100, max 500)"},
    },
    "additionalProperties": False,
}

_FIELDS = ("AlarmId", "MonitorAlarmID", "AlarmDate", "CreatedDate", "MachineName", "AgentName",
           "AgentId", "AlarmType", "MonitorType", "Message", "AlarmMessage", "AlarmState",
           "State", "Severity", "Status")
_NAME = ("MachineName", "AgentName")


def _is_open(a: dict) -> bool:
    s = " ".join(str(a.get(k) or "") for k in ("AlarmState", "State", "Status")).lower()
    return "clos" not in s and "ack" != s.strip()        # treat non-closed as open


def run(ctx, machine_contains: str = "", include_closed: bool = False, limit: int = 100,
        **_: Any):
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    limit = max(1, min(int(limit or 100), 500))
    # VSA 9 exposes alarms at /assetmgmt/alarms/{returnAllRecords} (true = all records, we filter
    # open/closed below) — NOT a bare /alarms (which 404s). Verified vs the live Swagger.
    data, e = k.result(client, "/assetmgmt/alarms/true", {"$top": limit})
    if e:
        return {"ok": False, "error": e}
    alarms = k.rows(data)
    if not include_closed:
        alarms = [a for a in alarms if _is_open(a)]
    needle = (machine_contains or "").strip().lower()
    if needle:
        alarms = [a for a in alarms
                  if needle in " ".join(str(a.get(x, "")) for x in _NAME).lower()]
    return {"ok": True, "count": len(alarms),
            "scope": "open" if not include_closed else "all",
            "alarms": [k.slim(a, _FIELDS) for a in alarms[:limit]]}
