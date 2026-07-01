"""Describe retention policies with their TAGS expanded inline (D-114 follow-up; SOP: exchange-online).

exo_list_retention_policies shows a policy's tag NAMES; exo_list_retention_tags shows what each tag
does. This tool joins the two so you see, in ONE read, what each policy actually DOES — every linked
tag expanded to its action + age + folder scope — to decide which policy to apply on a client that
doesn't already have a standard set. Read-only.
"""
from __future__ import annotations

from typing import Any, Optional

NAME = "exo_describe_retention_policies"
DESCRIPTION = ("Describe the client's retention POLICIES with their TAGS expanded inline — for each "
               "policy, every linked tag's action (delete-recoverable / PERMANENT-delete / "
               "move-to-archive), age in days, and folder scope, plus a one-line plain-English "
               "summary of the whole policy. Use this to SEE WHAT EACH POLICY DOES (not just its "
               "name) when deciding which to apply, e.g. on a client without a standard set. Pass "
               "`name` to describe one policy; omit it for all. Read-only.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string",
                 "description": "describe just this one policy by name (optional; default all)"},
    },
    "additionalProperties": False,
}

# Exchange RetentionAction -> plain English (mirrors exo_create_retention_tag's _ACTIONS, reversed)
_ACTION_LABEL = {
    "deleteandallowrecovery": "delete (recoverable)",
    "permanentlydelete": "delete (PERMANENT)",
    "movetoarchive": "move to archive",
    "markaspastretentionlimit": "mark past retention limit",
}


def _action_label(action: Any) -> str:
    a = str(action or "").strip()
    return _ACTION_LABEL.get(a.lower(), a or "—")


def _tag_summary(tag: dict) -> str:
    label = _action_label(tag.get("action"))
    scope = str(tag.get("applies_to") or "")
    age = tag.get("age_days")
    base = f"{label} @ {age}d" if age not in (None, "", 0) else f"{label} (no age limit)"
    out = f"{base} [{scope}]" if scope else base
    if tag.get("enabled") is False:
        out += " (disabled)"
    return out


def _expand_tag(t: dict) -> dict:
    tag = {"name": t.get("Name"),
           "applies_to": t.get("Type"),               # folder scope (All/Personal/Inbox/…)
           "action": t.get("RetentionAction"),
           "age_days": t.get("AgeLimitForRetention"),
           "enabled": t.get("RetentionEnabled")}
    tag["summary"] = _tag_summary(tag)
    return tag


def run(ctx, name: str = "", **_: Any):
    from . import _exo_common as c
    exo = ctx.client("exo")

    pol = exo.invoke("Get-RetentionPolicy", {"Identity": name.strip()} if (name or "").strip() else None)
    if c.err(pol):
        e = c.err(pol)
        if (name or "").strip() and c.is_not_found(e):
            return {"ok": False, "error": f"no retention policy named '{name.strip()}'"}
        return {"ok": False, "error": e}
    policies = [p for p in (pol if isinstance(pol, list) else [pol]) if isinstance(p, dict)]

    tags = exo.invoke("Get-RetentionPolicyTag")
    if c.err(tags):
        return {"ok": False, "step": "list tags", "error": c.err(tags)}
    by_name = {str(t.get("Name")).lower(): _expand_tag(t)
               for t in (tags if isinstance(tags, list) else [tags]) if isinstance(t, dict)}

    out_policies = []
    for p in policies:
        links = p.get("RetentionPolicyTagLinks") or []
        expanded, unresolved = [], []
        for link in links:
            tag = by_name.get(str(link).strip().lower())
            (expanded.append(tag) if tag else unresolved.append(str(link)))
        row: dict[str, Any] = {
            "name": p.get("Name"),
            "is_default": bool(p.get("IsDefault")),
            "tag_count": len(links),
            "tags": expanded,
            "summary": "; ".join(t["summary"] for t in expanded) or "(no tags linked)",
        }
        if unresolved:
            row["unresolved_tags"] = unresolved      # linked by name but not found in the tag list
        out_policies.append(row)

    return {"ok": True, "count": len(out_policies), "policies": out_policies}
