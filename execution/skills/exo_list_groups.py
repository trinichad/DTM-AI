"""List email groups — distribution + Microsoft 365 groups (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_list_groups"
DESCRIPTION = ("List the client's EMAIL GROUPS: distribution lists, mail-enabled security "
               "groups, and Microsoft 365 groups — name, email address, and kind. Use before "
               "exo_add_group_member to find the right group. Pass `identity` for one group.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "one group by name/address (optional)"},
        "limit": {"type": "integer", "description": "max per kind (default 100, max 500)"},
    },
    "additionalProperties": False,
}


def _fetch(exo, cmdlet: str, kind: str, identity: str, limit: int):
    from . import _exo_common as c
    params: dict[str, Any] = {"ResultSize": limit}
    if identity:
        params["Identity"] = identity
    r = exo.invoke(cmdlet, params)
    e = c.err(r)
    if e:
        return [], (None if c.is_not_found(e) else e)   # a miss on one kind is not an error
    rows = [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]
    return [{"name": g.get("DisplayName") or g.get("Name"),
             "email": g.get("PrimarySmtpAddress"),
             "kind": kind} for g in rows], None


def run(ctx, identity: str = "", limit: int = 100, **_: Any):
    limit = max(1, min(int(limit or 100), 500))
    identity = (identity or "").strip()
    exo = ctx.client("exo")
    groups, errors = [], []
    for cmdlet, kind in (("Get-DistributionGroup", "distribution"),
                         ("Get-UnifiedGroup", "microsoft365")):
        rows, e = _fetch(exo, cmdlet, kind, identity, limit)
        groups.extend(rows)
        if e:
            errors.append({"kind": kind, "error": e[:200]})
    out: dict[str, Any] = {"count": len(groups), "groups": groups}
    if identity and not groups:
        out["note"] = f"no group matching '{identity}'"
    if errors:
        out["errors"] = errors
    return out
