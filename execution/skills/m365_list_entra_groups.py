"""List directory (Entra) groups — security / M365 / dynamic (D-56; SOP: m365-graph)."""
from __future__ import annotations

from typing import Any

NAME = "m365_list_entra_groups"
DESCRIPTION = ("List the client's DIRECTORY (Entra) groups: kind='security' (security groups), "
               "'m365' (Microsoft 365 groups), 'dynamic' (rule-based membership), or 'all'. "
               "Shows name, email, kind, and the membership rule for dynamic groups. For "
               "EMAIL distribution lists use exo_list_groups instead.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
_KINDS = ("all", "security", "m365", "dynamic")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(_KINDS),
                 "description": "which groups (default all)"},
        "search": {"type": "string", "description": "name contains this text (optional)"},
        "limit": {"type": "integer", "description": "max results (default 100, max 500)"},
    },
    "additionalProperties": False,
}


def classify(grp: dict) -> str:
    types = [str(t) for t in (grp.get("groupTypes") or [])]
    if "DynamicMembership" in types:
        return "dynamic"
    if "Unified" in types:
        return "m365"
    if grp.get("securityEnabled") and not grp.get("mailEnabled"):
        return "security"
    if grp.get("mailEnabled"):
        return "distribution/mail-enabled"
    return "other"


def run(ctx, kind: str = "all", search: str = "", limit: int = 100, **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read
    from . import _graph_common as g
    kind = (kind or "all").strip().lower()
    if kind not in _KINDS:
        return {"ok": False, "error": f"kind must be one of: {', '.join(_KINDS)}"}
    limit = max(1, min(int(limit or 100), 500))
    params: dict[str, Any] = {
        "$select": "id,displayName,mail,mailNickname,securityEnabled,mailEnabled,"
                   "groupTypes,membershipRule,membershipRuleProcessingState",
        "$top": limit, "$count": "true"}
    q = (search or "").strip().replace('"', "")
    if q:
        params["$search"] = f'"displayName:{q}"'
    try:
        data = scoped_read(ctx, "m365", "/groups", params)
    except HttpError as exc:
        return g.err403(exc, "listing groups", "Group.Read.All")
    bad = g.fail(data)
    if bad:
        return bad

    groups = []
    for grp in g.rows(data):
        k = classify(grp)
        if kind != "all" and k != kind:
            continue
        row: dict[str, Any] = {"name": grp.get("displayName"), "id": grp.get("id"),
                               "email": grp.get("mail"), "kind": k}
        if k == "dynamic":
            row["membership_rule"] = grp.get("membershipRule")
            row["rule_processing"] = grp.get("membershipRuleProcessingState")
        groups.append(row)
    out: dict[str, Any] = {"count": len(groups), "kind": kind, "groups": groups}
    if q:
        out["searched_for"] = q
    if isinstance(data, dict) and data.get("@odata.nextLink"):
        out["note"] = "more groups exist beyond this page — narrow with `search`"
    return out
