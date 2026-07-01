"""List Google Workspace organizational units via the Admin SDK Directory API (D-118) — read-only."""
from __future__ import annotations

from typing import Any

NAME = "gws_list_org_units"
DESCRIPTION = ("List Google Workspace organizational units (OUs) — path, name, description. OUs are "
               "how users are grouped for policy/licensing. Scoped to the selected client; on "
               "'All clients' (*) it aggregates across every signed-in client, tagging each OU with "
               "its `tenant`.")
SOURCE = "gws"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True

_PATH = "/admin/directory/v1/customer/my_customer/orgunits"
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["all", "children"],
                 "description": "'all' (every OU, default) or 'children' (top-level only)"},
    },
    "additionalProperties": False,
}


def _slim(o: dict) -> dict:
    out: dict[str, Any] = {"orgUnitPath": o.get("orgUnitPath"), "name": o.get("name")}
    for k in ("description", "parentOrgUnitPath", "tenant"):
        if o.get(k):
            out[k] = o[k]
    return out


def run(ctx, type: str = "all", **_: Any):
    from ._gws_common import read_list
    t = type if type in ("all", "children") else "all"
    # orgunits.list returns a flat {"organizationUnits":[...]} (no paging) — read_list handles it.
    return read_list(ctx, _PATH, {"type": t}, "organizationUnits", _slim, out_key="org_units")
