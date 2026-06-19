"""Remote-control session history for a Kaseya-managed machine (D-68; SOP: kaseya-vsa).

Answers "who connected remotely to X, and when" from Kaseya's remote-control log
(`/assetmgmt/logs/{agentId}/remotecontrol`) — the admin, the session start + last-active times,
the session type, and the admin/endpoint IPs when present. Most recent session first.
"""
from __future__ import annotations

from typing import Any

NAME = "kaseya_remote_session_history"
DESCRIPTION = ("Show the REMOTE-CONTROL session history for one machine — who connected to it "
               "remotely via Kaseya (Live Connect / Remote Control) and WHEN, with each session's "
               "start time, last-active time, the administrator, and IPs when recorded. Most recent "
               "first. Use for 'who connected remotely to X', 'when was X last accessed remotely', "
               "'last remote session on X'. Pass the machine name or AgentId.")
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
        "limit": {"type": "integer", "description": "max sessions to return (default 50, max 500)"},
        "include_legacy": {"type": "boolean",
                           "description": "also include the legacy remote-control log (default "
                                          "false) — older/other RC tooling"},
    },
    "required": ["machine"],
    "additionalProperties": False,
}

_FIELDS = ("StartTime", "LastActiveTime", "Administrator", "SessionType",
           "AdminIP", "EndpointIP", "Activity", "LogEntry")


def _start(row: dict) -> str:
    # ISO timestamps sort correctly as strings; missing → sorts last.
    return str(row.get("StartTime") or "")


def run(ctx, machine: str, limit: int = 50, include_legacy: bool = False, **_: Any):
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    try:
        limit = max(1, min(int(limit or 50), 500))
    except (TypeError, ValueError):
        limit = 50
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "error": err}
    aid = agent.get("AgentId")

    rows: list[dict] = []
    errors: list[str] = []
    sources = ["remotecontrol"] + (["legacyremotecontrol"] if include_legacy else [])
    for src in sources:
        data, e = k.result(client, f"/assetmgmt/logs/{aid}/{src}")
        if e:
            errors.append(f"{src}: {e}")
            continue
        for r in k.rows(data):
            r = dict(r)
            r.setdefault("_log", src)
            rows.append(r)

    # all requested logs errored → a real failure, not just an empty history
    if errors and not rows and len(errors) == len(sources):
        return {"ok": False, "error": "; ".join(errors)}

    rows.sort(key=_start, reverse=True)
    sessions = [k.slim(r, _FIELDS) for r in rows[:limit]]
    out: dict[str, Any] = {
        "ok": True,
        "machine": agent.get("AgentName") or agent.get("ComputerName"),
        "agent_id": aid,
        "session_count": len(sessions),
        "sessions": sessions,
    }
    if sessions:
        top = rows[0]
        out["last_remote_connection"] = {
            "administrator": top.get("Administrator"),
            "start_time": top.get("StartTime"),
            "last_active_time": top.get("LastActiveTime"),
        }
    else:
        out["note"] = "no remote-control sessions are recorded for this machine"
    if errors:
        out["partial_errors"] = errors
    return out
