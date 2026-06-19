"""Add/remove retention TAGS on an existing retention POLICY (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_update_retention_policy_tags"
DESCRIPTION = ("ADD retention tags to (or REMOVE them from) an EXISTING retention policy. "
               "Tags to add must already exist (exo_list_retention_tags). The change reaches "
               "every mailbox using the policy on the next Managed Folder Assistant cycle. "
               "Verifies the policy's tag list before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "policy": {"type": "string",
                   "description": "the retention policy to edit (exact name — see "
                                  "exo_list_retention_policies)"},
        "add_tags": {"type": "array", "items": {"type": "string"},
                     "description": "tag names to add (optional)"},
        "remove_tags": {"type": "array", "items": {"type": "string"},
                        "description": "tag names to remove (optional)"},
    },
    "required": ["policy"],
    "additionalProperties": False,
}


def _clean(v: Any) -> list[str]:
    return [str(x).strip() for x in (v or []) if str(x or "").strip()] \
        if isinstance(v, list) else []


def run(ctx, policy: str, add_tags: Any = None, remove_tags: Any = None, **_: Any):
    from ..clients.exo import hashtable
    from . import _exo_common as c
    from .exo_create_retention_policy import resolve_tags
    policy = (policy or "").strip()
    adds, removes = _clean(add_tags), _clean(remove_tags)
    if not adds and not removes:
        return {"ok": False, "error": "give add_tags and/or remove_tags"}
    exo = ctx.client("exo")

    cur = exo.invoke("Get-RetentionPolicy", {"Identity": policy})
    rows = [x for x in (cur if isinstance(cur, list) else [cur]) if isinstance(x, dict)]
    if c.err(cur) or not rows:
        e = c.err(cur)
        if c.is_not_found(e) or not rows:
            return {"ok": False, "error": f"no retention policy '{policy}' — see "
                                          f"exo_list_retention_policies"}
        return {"ok": False, "step": "read policy", "error": e}
    links_before = [str(t) for t in (rows[0].get("RetentionPolicyTagLinks") or [])]

    if adds:
        adds, bad = resolve_tags(exo, adds)               # tags to add must really exist
        if bad:
            return bad
    ht: dict[str, Any] = {}
    if adds:
        ht["Add"] = adds
    if removes:
        ht["Remove"] = removes
    r = exo.invoke("Set-RetentionPolicy", {"Identity": policy, "Confirm": False,
                                           "RetentionPolicyTagLinks": hashtable(ht)})
    if c.err(r):
        return {"ok": False, "step": "update", "error": c.err(r)}

    check = exo.invoke("Get-RetentionPolicy", {"Identity": policy})
    rows2 = [x for x in (check if isinstance(check, list) else [check]) if isinstance(x, dict)]
    if c.err(check) or not rows2:
        return {"ok": False, "step": "verify", "error": "the policy could not be re-read — "
                                                        "check Exchange directly"}
    links_after = [str(t) for t in (rows2[0].get("RetentionPolicyTagLinks") or [])]
    lower_after = {t.lower() for t in links_after}
    missing_adds = [t for t in adds if t.lower() not in lower_after]
    lingering = [t for t in removes if t.lower() in lower_after]
    if missing_adds or lingering:
        return {"ok": False, "step": "verify", "tags_now": links_after,
                "error": "the update returned no error but the tag list doesn't match: "
                         + (f"not added: {', '.join(missing_adds)}; " if missing_adds else "")
                         + (f"still present: {', '.join(lingering)}" if lingering else "")}
    return {"ok": True, "policy": policy,
            "tags_before": links_before, "tags_now": links_after,
            "note": "mailboxes on this policy pick the change up on the next Managed Folder "
                    "Assistant cycle (up to ~7 days)"}
