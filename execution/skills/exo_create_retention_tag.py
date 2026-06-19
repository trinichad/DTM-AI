"""Create an Exchange retention TAG (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_create_retention_tag"
DESCRIPTION = ("Create a retention TAG — one rule like 'delete after 90 days' or 'move to "
               "archive after 2 years'. applies_to is the folder scope (All = whole mailbox, "
               "Personal = users apply it themselves, or a specific folder like Inbox / "
               "DeletedItems). action: delete_allow_recovery (recoverable ~14-30 days), "
               "permanent_delete (NOT recoverable), or move_to_archive (All/Personal scope "
               "only). Add the tag to a policy with exo_update_retention_policy_tags or "
               "exo_create_retention_policy.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

_ACTIONS = {"delete_allow_recovery": "DeleteAndAllowRecovery",
            "permanent_delete": "PermanentlyDelete",
            "move_to_archive": "MoveToArchive"}
_SCOPES = ("All", "Personal", "Inbox", "SentItems", "DeletedItems", "Drafts", "JunkEmail",
           "Outbox", "Notes", "Calendar", "Tasks", "RecoverableItems")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "tag name, e.g. 'Delete after 90 days'"},
        "applies_to": {"type": "string", "enum": list(_SCOPES),
                       "description": "folder scope (All = default tag for the whole mailbox; "
                                      "Personal = optional tag users apply themselves)"},
        "action": {"type": "string", "enum": list(_ACTIONS),
                   "description": "what happens when mail reaches the age"},
        "age_days": {"type": "integer", "description": "age in days that triggers the action "
                                                       "(1–24855)"},
    },
    "required": ["name", "applies_to", "action", "age_days"],
    "additionalProperties": False,
}


def run(ctx, name: str, applies_to: str, action: str, age_days: int, **_: Any):
    from . import _exo_common as c
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "the tag needs a name"}
    scope = next((s for s in _SCOPES if s.lower() == (applies_to or "").strip().lower()), None)
    if not scope:
        return {"ok": False, "error": f"applies_to must be one of: {', '.join(_SCOPES)}"}
    ra = _ACTIONS.get((action or "").strip().lower())
    if not ra:
        return {"ok": False, "error": f"action must be one of: {', '.join(_ACTIONS)}"}
    if ra == "MoveToArchive" and scope not in ("All", "Personal", "RecoverableItems"):
        return {"ok": False, "error": "move_to_archive tags must have applies_to All, Personal, "
                                      "or RecoverableItems (an Exchange rule — other folder "
                                      "tags can't archive)"}
    try:
        age_days = int(age_days)
    except (TypeError, ValueError):
        return {"ok": False, "error": "age_days must be a number of days"}
    if not 1 <= age_days <= 24855:
        return {"ok": False, "error": "age_days must be between 1 and 24855"}

    exo = ctx.client("exo")
    existing = exo.invoke("Get-RetentionPolicyTag")
    if not c.err(existing):
        names = [str(t.get("Name")) for t in (existing if isinstance(existing, list) else [])
                 if isinstance(t, dict)]
        if any(n.lower() == name.lower() for n in names):
            return {"ok": False, "error": f"a retention tag named '{name}' already exists — "
                                          f"pick another name"}

    r = exo.invoke("New-RetentionPolicyTag", {
        "Name": name, "Type": scope, "RetentionAction": ra,
        "AgeLimitForRetention": age_days, "RetentionEnabled": True, "Confirm": False})
    if c.err(r):
        return {"ok": False, "step": "create", "error": c.err(r)}

    check = exo.invoke("Get-RetentionPolicyTag")
    rows = [x for x in (check if isinstance(check, list) else [check]) if isinstance(x, dict)]
    made = next((t for t in rows if str(t.get("Name", "")).lower() == name.lower()), None)
    if not made:
        return {"ok": False, "step": "verify",
                "error": f"New-RetentionPolicyTag returned no error but '{name}' is not in the "
                         f"tag list — check Exchange directly"}
    out: dict[str, Any] = {"ok": True, "created": name, "applies_to": scope,
                           "action": action, "age_days": age_days,
                           "next": "the tag does nothing until it's in a policy — add it with "
                                   "exo_update_retention_policy_tags or build a new policy with "
                                   "exo_create_retention_policy"}
    if ra == "PermanentlyDelete":
        out["warning"] = ("PERMANENT delete — items past the age are NOT recoverable once "
                          "processed; make sure this is intended")
    return out
