"""schedule_task — let the lead set up a RECURRING delegated job by talking (SOP: scheduled-delegation).

CATEGORY=write, SOURCE=dtm_ai: this writes only to DTM AI's OWN delegation board, never to a client
system. But unlike memory_note it creates *future autonomous work*, so it ships REQUIRES_APPROVAL=True
and ENABLED_BY_DEFAULT=False: the owner opts the lead into scheduling via the Capability Console, and
(until they flip require_approval off) each new schedule waits for an approval. The scheduled job then
runs as the assigned specialist on its own brain, through the same guarded agent loop.
"""
from __future__ import annotations

import time
from typing import Any

from execution.core.scheduler import compute_next_run, valid_spec
from execution.core.tasks import TaskStore

NAME = "schedule_task"
DESCRIPTION = (
    "Create a RECURRING scheduled job on the delegation board, run automatically by a specialist "
    "agent on a cadence (e.g. a daily security check). Use ONLY when the owner asks to set up a "
    "repeating/automated task — answer one-off questions directly instead. Results appear on the "
    "Delegation board."
)
SOURCE = "dtm_ai"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short name for the job, e.g. 'RHO Cylance version check'."},
        "instructions": {"type": "string", "description": "Exactly what the specialist should do each run, in plain language."},
        "assignee": {"type": "string", "description": "The specialist profile id that runs it, e.g. 'patchwright'."},
        "schedule": {"type": "string", "description": "Cadence: 'every 30m', 'hourly', 'daily 07:00', or 'weekdays 09:30'."},
    },
    "required": ["title", "assignee", "schedule"],
    "additionalProperties": False,
}


def run(ctx, title: str, assignee: str, schedule: str, instructions: str = "", **_: Any):
    title = (title or "").strip()
    assignee = (assignee or "").strip()
    schedule = (schedule or "").strip()
    if not title:
        return {"ok": False, "error": "a title is required"}
    if not assignee:
        return {"ok": False, "error": "an assignee (specialist profile id) is required"}
    if not valid_spec(schedule):
        return {"ok": False, "error": f"unrecognised schedule '{schedule}'. Try "
                "'every 30m', 'hourly', 'daily 07:00', or 'weekdays 09:30'."}
    # Confirm the specialist exists — a clearer error now than a silently-failing run later.
    from execution.core.agents import get_agent
    try:
        if get_agent(assignee) is None:
            return {"ok": False, "error": f"no specialist agent '{assignee}' — create it in the Agents tab first"}
    except ValueError:
        return {"ok": False, "error": f"invalid agent name '{assignee}'"}

    next_run = compute_next_run(schedule, int(time.time() * 1000))
    store = (getattr(ctx, "_meta", None) or {}).get("tasks") or TaskStore()
    try:
        task = store.create(title=title, body=instructions or "", assignee=assignee,
                            created_by=ctx.actor, tenant=ctx.tenant_id,
                            recurring=True, schedule_spec=schedule, next_run_at=next_run)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "task_id": task["id"], "assignee": assignee, "schedule": schedule,
            "next_run_ms": next_run, "status": task["status"],
            "note": "Scheduled. It runs automatically on the cadence; results appear on the Delegation board."}
