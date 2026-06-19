"""Kaseya org structure — departments, locations, staff (D-68; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_org_structure"
DESCRIPTION = ("Show a client ORGANIZATION's structure in Kaseya: its departments, site "
               "locations, and staff/contacts. Pass `org` (name or OrgId) for one org; leave "
               "empty to list all organizations. Use for 'what departments/sites does client X "
               "have' or 'who are the staff contacts'.")
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "org": {"type": "string", "description": "organization name or OrgId (optional — empty "
                                                 "lists all orgs)"},
    },
    "additionalProperties": False,
}

_ORG = ("OrgId", "OrgRef", "OrgName", "DefaultDepartmentName", "OrgType")
_DEPT = ("DepartmentId", "DepartmentName", "ParentDepartmentName")
_LOC = ("LocationName", "Name", "Street", "City", "State", "Country", "ZipCode", "PhoneNumber")
_STAFF = ("StaffId", "AdminName", "FullName", "Title", "Email", "Phone", "DepartmentName")


def _resolve_org(client, needle):
    from . import _kaseya_common as k
    data, e = k.result(client, "/system/orgs")
    if e:
        return None, None, e
    orgs = k.rows(data)
    n = needle.strip().lower()
    for o in orgs:
        if str(o.get("OrgId")) == needle.strip() or str(o.get("OrgName") or "").lower() == n \
                or str(o.get("OrgRef") or "").lower() == n:
            return o, orgs, None
    hits = [o for o in orgs if n in str(o.get("OrgName") or "").lower()
            or n in str(o.get("OrgRef") or "").lower()]
    if len(hits) == 1:
        return hits[0], orgs, None
    if not hits:
        return None, orgs, f"no Kaseya org matched '{needle}'"
    names = [str(o.get("OrgName") or o.get("OrgId")) for o in hits[:6]]
    return None, orgs, f"'{needle}' matched {len(hits)} orgs — be specific: {', '.join(names)}"


def run(ctx, org: str = "", **_: Any):
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    if not (org or "").strip():
        data, e = k.result(client, "/system/orgs")
        if e:
            return {"ok": False, "error": e}
        return {"ok": True, "count": len(k.rows(data)),
                "organizations": [k.slim(o, _ORG) for o in k.rows(data)],
                "note": "pass one org name/id as `org` for its departments, locations, and staff"}

    o, _orgs, e = _resolve_org(client, org)
    if e:
        return {"ok": False, "error": e}
    oid = o.get("OrgId")
    out: dict[str, Any] = {"ok": True, "org": o.get("OrgName"), "org_id": oid,
                           "organization": k.slim(o, _ORG)}
    depts, e = k.result(client, f"/system/orgs/{oid}/departments")
    out["departments"] = [k.slim(r, _DEPT) for r in k.rows(depts)] if not e else []
    locs, e = k.result(client, f"/system/orgs/{oid}/locations")
    out["locations"] = [k.slim(r, _LOC) for r in k.rows(locs)] if not e else []
    staff, e = k.result(client, f"/system/orgs/{oid}/staff")
    out["staff"] = [k.slim(r, _STAFF) for r in k.rows(staff)] if not e else []
    return out
