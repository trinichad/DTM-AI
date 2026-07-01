"""Query Google Workspace audit activity via the Reports API (D-118) — read-only."""
from __future__ import annotations

from typing import Any

NAME = "gws_audit_log"
DESCRIPTION = ("Read Google Workspace audit activity from the Reports API — who did what, when. "
               "`application` picks the log: login (sign-ins, incl. failures/suspicious), admin "
               "(admin-console changes), drive (file access/sharing), token (OAuth grants), "
               "user_accounts, groups, mobile. `user` filters to one person (default 'all'). Returns "
               "recent events, most-recent first. Scoped to the selected client.")
SOURCE = "gws"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True

_APPS = ("login", "admin", "drive", "token", "user_accounts", "groups", "mobile", "calendar")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "application": {"type": "string", "enum": list(_APPS),
                        "description": "which audit log to read (default login)"},
        "user": {"type": "string",
                 "description": "a user's email to filter to, or 'all' (default all users)"},
        "max": {"type": "integer", "description": "max events to return (1–500, default 100)"},
    },
    "additionalProperties": False,
}


def _slim(a: dict) -> dict:
    actor = (a.get("actor") or {}) if isinstance(a.get("actor"), dict) else {}
    ident = (a.get("id") or {}) if isinstance(a.get("id"), dict) else {}
    events = []
    for ev in (a.get("events") or []):
        if not isinstance(ev, dict):
            continue
        row = {"name": ev.get("name"), "type": ev.get("type")}
        params = {p.get("name"): (p.get("value") or p.get("boolValue") or p.get("multiValue"))
                  for p in (ev.get("parameters") or []) if isinstance(p, dict) and p.get("name")}
        if params:
            row["details"] = {k: v for k, v in params.items() if v not in (None, "")}
        events.append(row)
    return {"time": ident.get("time"), "actor": actor.get("email") or actor.get("callerType"),
            "ip": a.get("ipAddress"), "events": events}


def run(ctx, application: str = "login", user: str = "all", max: int = 100, **_: Any):
    from execution.clients.scopes import scoped_read
    app = application if application in _APPS else "login"
    who = (user or "all").strip() or "all"
    try:
        top = int(max)
    except (TypeError, ValueError):
        top = 100
    top = 100 if top < 1 else min(top, 500)
    path = f"/admin/reports/v1/activity/users/{who}/applications/{app}"
    data = scoped_read(ctx, "gws", path, {"maxResults": top})
    if isinstance(data, dict) and data.get("error"):
        return data
    items = (data.get("items") if isinstance(data, dict) else None) or []
    rows = [_slim(a) for a in items if isinstance(a, dict)]
    out: dict[str, Any] = {"application": app, "user": who, "count": len(rows), "events": rows}
    if isinstance(data, dict) and data.get("nextPageToken"):
        out["note"] = "more events exist beyond this page — narrow by user or reduce the time window"
    return out
