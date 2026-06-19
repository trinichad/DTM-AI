"""Create an Exchange retention POLICY from existing tags (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_create_retention_policy"
DESCRIPTION = ("Create a retention POLICY — a named bundle of existing retention TAGS that can "
               "then be applied to mailboxes with exo_set_retention_policy. Every tag named in "
               "`tags` must already exist (see exo_list_retention_tags / "
               "exo_create_retention_tag). Verifies the policy before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "policy name, e.g. 'Standard 1 Year'"},
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "names of EXISTING retention tags to include"},
    },
    "required": ["name", "tags"],
    "additionalProperties": False,
}


def _names(rows: Any, field: str = "Name") -> list[str]:
    return [str(x.get(field)) for x in (rows if isinstance(rows, list) else [rows])
            if isinstance(x, dict) and x.get(field)]


def resolve_tags(exo, wanted: list) -> tuple[list[str], dict | None]:
    """Match requested tag names against the tenant's real tags (case-insensitive, exact)."""
    from . import _exo_common as c
    existing = exo.invoke("Get-RetentionPolicyTag")
    if c.err(existing):
        return [], {"ok": False, "step": "list tags", "error": c.err(existing)}
    have = _names(existing)
    by_lower = {h.lower(): h for h in have}
    matched, missing = [], []
    for w in wanted:
        hit = by_lower.get(str(w or "").strip().lower())
        (matched.append(hit) if hit else missing.append(str(w)))
    if missing:
        return [], {"ok": False, "error": f"no retention tag(s) named: {', '.join(missing)} — "
                                          f"existing tags: {', '.join(have) or '(none)'} "
                                          f"(create one with exo_create_retention_tag)"}
    return matched, None


def run(ctx, name: str, tags: list, **_: Any):
    from . import _exo_common as c
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "the policy needs a name"}
    if not isinstance(tags, list) or not [t for t in tags if str(t or "").strip()]:
        return {"ok": False, "error": "give at least one retention tag for the policy"}
    exo = ctx.client("exo")

    existing = exo.invoke("Get-RetentionPolicy")
    if not c.err(existing) and any(n.lower() == name.lower() for n in _names(existing)):
        return {"ok": False, "error": f"a retention policy named '{name}' already exists — "
                                      f"edit it with exo_update_retention_policy_tags instead"}
    matched, bad = resolve_tags(exo, tags)
    if bad:
        return bad

    r = exo.invoke("New-RetentionPolicy", {"Name": name, "RetentionPolicyTagLinks": matched,
                                           "Confirm": False})
    if c.err(r):
        return {"ok": False, "step": "create", "error": c.err(r)}

    check = exo.invoke("Get-RetentionPolicy", {"Identity": name})
    rows = [x for x in (check if isinstance(check, list) else [check]) if isinstance(x, dict)]
    if c.err(check) or not rows:
        return {"ok": False, "step": "verify",
                "error": f"New-RetentionPolicy returned no error but '{name}' could not be "
                         f"read back — check Exchange directly"}
    return {"ok": True, "created": name,
            "tags": [str(t) for t in (rows[0].get("RetentionPolicyTagLinks") or [])],
            "next": "apply it to a mailbox with exo_set_retention_policy"}
