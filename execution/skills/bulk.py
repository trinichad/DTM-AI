"""Run ONE tool many times in a single tool call (D-111).

`bulk` is a META-tool: it doesn't touch any client itself. dispatch() intercepts it and
re-enters dispatch() once per item, so EVERY per-item run passes the full guardrail stack
(schema validation, the I-4 kill switch, CATEGORY + approval gating, tenant isolation, and a
per-call audit record). It exists to collapse the N-round "call the same tool over and over"
loop — which both burns the tool-call budget and reads as broken — into a single call.

Because each item re-enters dispatch, bulk grants NO new authority: a write still needs the
tenant's write flag (and a trusted/auto-approve policy or a D-59 batch grant) and a destructive
tool still requires its per-action approval (Rule #1 floor) — bulk just stops asking for each
one in its own round. If an item needs human approval, bulk surfaces that one card and pauses;
re-invoke bulk after the owner decides and it continues (already-done items re-run harmlessly —
the underlying tools verify/self-heal and treat "already in that state" as success).
"""
from __future__ import annotations

from typing import Any

NAME = "bulk"
DESCRIPTION = ("Run ONE tool many times in a SINGLE call. Use this WHENEVER you would otherwise "
               "call the same tool repeatedly (e.g. the same action for a list of users, "
               "mailboxes, machines, devices, or tickets) — do NOT emit the same tool call over "
               "and over. Pass `tool` = the tool name and `items` = a list of argument-objects "
               "(one per run); the results come back together as `results[]` (each tagged with "
               "its index). Works for reads AND writes; every item still goes through the same "
               "permission/approval checks as calling it directly. Prefer a tool's own native "
               "list parameter (e.g. m365_list_users `names`, m365_mfa_status `users`) when it "
               "has one; otherwise use bulk.")
SOURCE = "msp_ai"
CATEGORY = "read"            # the meta-tool is inert; each item's REAL category is gated per-item
RISK_LEVEL = "none"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool": {"type": "string",
                 "description": "the tool to run for each item (any tool except 'bulk')"},
        "items": {"type": "array", "items": {"type": "object"},
                  "description": "one argument-object per run — exactly the args you'd pass when "
                                 "calling `tool` directly, e.g. "
                                 "[{\"user\": \"a@x.com\"}, {\"user\": \"b@x.com\"}]"},
    },
    "required": ["tool", "items"],
    "additionalProperties": False,
}


def run(ctx, **kwargs: Any):  # pragma: no cover - dispatch() intercepts 'bulk' before run()
    return {"ok": False, "error": "bulk is handled by dispatch(); it should never reach run()"}
