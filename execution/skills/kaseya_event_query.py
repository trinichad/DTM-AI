"""Query a machine's Windows Event Log — event viewer (D-79; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_event_query"
DESCRIPTION = ("Read entries from a machine's Windows Event Log (event viewer). Give the `server` "
               "(the machine to read), and optionally: `log` (System, Application, Security, "
               "Setup, or any log name — default System), `level` (critical/error/warning/"
               "information), `since_hours` (only events newer than N hours), `event_id`, "
               "`provider` (source), and `count` (max events, default 50). Read-only. Read the "
               "result with kaseya_command_output.")
SOURCE = "kaseya"
GROUP = "kaseya_events"
CATEGORY = "write"            # read-only in effect; runs a command on the endpoint
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_LOG_RE = re.compile(r'^[A-Za-z0-9 /._-]{1,255}$')      # e.g. "System", "Microsoft-Windows-.../Operational"
_PROVIDER_RE = re.compile(r'^[A-Za-z0-9 ._-]{1,255}$')
_LEVELS = {"critical": 1, "error": 2, "warning": 3, "information": 4, "info": 4}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "the machine to read events from (name/AgentId)"},
        "log": {"type": "string", "description": "log name (default System)"},
        "level": {"type": "string", "enum": ["critical", "error", "warning", "information"],
                  "description": "minimum severity filter (optional)"},
        "since_hours": {"type": "integer", "minimum": 1, "maximum": 720,
                        "description": "only events newer than N hours (optional)"},
        "event_id": {"type": "integer", "minimum": 0, "maximum": 65535,
                     "description": "filter by a specific Event ID (optional)"},
        "provider": {"type": "string", "description": "filter by provider/source name (optional)"},
        "count": {"type": "integer", "minimum": 1, "maximum": 500,
                  "description": "max events to return (default 50)"},
    },
    "required": ["server"],
    "additionalProperties": False,
}


def run(ctx, server: str, log: str = "System", level: str = "", since_hours: Any = None,
        event_id: Any = None, provider: str = "", count: Any = None, **_: Any):
    from . import _kaseya_common as k
    log_v = str(log or "System").strip() or "System"
    if not _LOG_RE.match(log_v):
        return {"ok": False, "error": "the log name has invalid characters"}

    filt = ["LogName=" + k.ps_quote(log_v)]
    if (level or "").strip():
        lv = _LEVELS.get(level.strip().lower())
        if not lv:
            return {"ok": False, "error": "level must be critical, error, warning, or information"}
        filt.append("Level=" + str(lv))
    if event_id is not None:
        try:
            eid = int(event_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "event_id must be a number"}
        if not 0 <= eid <= 65535:
            return {"ok": False, "error": "event_id must be between 0 and 65535"}
        filt.append("Id=" + str(eid))
    if (provider or "").strip():
        pv = provider.strip()
        if not _PROVIDER_RE.match(pv):
            return {"ok": False, "error": "the provider name has invalid characters"}
        filt.append("ProviderName=" + k.ps_quote(pv))
    if since_hours is not None:
        try:
            hrs = int(since_hours)
        except (TypeError, ValueError):
            return {"ok": False, "error": "since_hours must be a number"}
        if not 1 <= hrs <= 720:
            return {"ok": False, "error": "since_hours must be between 1 and 720"}
        filt.append("StartTime=(Get-Date).AddHours(-" + str(hrs) + ")")

    n = 50
    if count is not None:
        try:
            n = int(count)
        except (TypeError, ValueError):
            return {"ok": False, "error": "count must be a number"}
        if not 1 <= n <= 500:
            return {"ok": False, "error": "count must be between 1 and 500"}

    cmd = ("try { Get-WinEvent -FilterHashtable @{ " + "; ".join(filt) + " } -MaxEvents " +
           str(n) + " -ErrorAction Stop | Select-Object TimeCreated,Id,LevelDisplayName,"
           "ProviderName,@{N='Message';E={($_.Message -split \"`n\")[0]}} | Format-Table "
           "-AutoSize -Wrap | Out-String -Width 4096 } catch { if ($_.Exception.Message -match "
           "'No events were found') { 'No matching events.' } else { 'ERROR: ' + "
           "$_.Exception.Message } }")
    out = k.run_command(ctx, server, cmd)
    if out.get("ok"):
        out.update({"log": log_v, "max_events": n})
        out["note"] = "read-only — read the events with kaseya_command_output"
    return out
