"""List the client's Exchange retention TAGS (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_list_retention_tags"
DESCRIPTION = ("List the client's Exchange retention TAGS — the individual rules ('delete after "
               "90 days', 'archive after 2 years') that retention POLICIES are built from. "
               "Shows each tag's folder scope, action, and age. Use before building or editing "
               "a policy.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {},
                              "additionalProperties": False}


def run(ctx, **_: Any):
    from . import _exo_common as c
    r = ctx.client("exo").invoke("Get-RetentionPolicyTag")
    if c.err(r):
        return {"ok": False, "error": c.err(r)}
    rows = [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]
    tags = [{"name": t.get("Name"),
             "applies_to": t.get("Type"),                 # folder scope (All/Inbox/Personal/…)
             "action": t.get("RetentionAction"),
             "age_days": t.get("AgeLimitForRetention"),
             "enabled": t.get("RetentionEnabled")} for t in rows]
    return {"count": len(tags), "tags": tags,
            "note": "applies_to=Personal tags are the ones users can apply themselves; "
                    "All/Inbox/etc. apply automatically to that scope"}
