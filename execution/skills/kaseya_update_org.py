"""Update a Kaseya organization's details (D-69; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_update_org"
DESCRIPTION = ("Update an existing Kaseya ORGANIZATION's details — display name, website, "
               "employee count. Pass the org (name or OrgId) and the fields to change.")
SOURCE = "kaseya"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "org": {"type": "string", "description": "organization name or OrgId"},
        "name": {"type": "string", "description": "new display name (optional)"},
        "website": {"type": "string", "description": "website (optional)"},
        "employees": {"type": "integer", "description": "number of employees (optional)"},
    },
    "required": ["org"],
    "additionalProperties": False,
}


def _find_org(client, needle):
    from . import _kaseya_common as k
    data, e = k.result(client, "/system/orgs")
    if e:
        return None, e
    n = str(needle).strip().lower()
    for o in k.rows(data):
        if str(o.get("OrgId")) == str(needle).strip() \
                or str(o.get("OrgName") or "").lower() == n \
                or str(o.get("OrgRef") or "").lower() == n:
            return o, None
    return None, f"no Kaseya org matched '{needle}'"


def run(ctx, org: str, name: str = "", website: str = "", employees: Any = None, **_: Any):
    client = ctx.client("kaseya")
    o, err = _find_org(client, org)
    if err:
        return {"ok": False, "error": err}
    body: dict[str, Any] = {"OrgId": o.get("OrgId"), "OrgName": o.get("OrgName"),
                            "OrgRef": o.get("OrgRef")}
    changed = []
    if (name or "").strip():
        body["OrgName"] = name.strip(); changed.append("name")
    if (website or "").strip():
        body["Website"] = website.strip(); changed.append("website")
    if employees is not None:
        try:
            body["NoOfEmployees"] = int(employees); changed.append("employees")
        except (TypeError, ValueError):
            return {"ok": False, "error": "employees must be a number"}
    if not changed:
        return {"ok": False, "error": "give a field to change (name, website, employees)"}
    r = client.write("PUT", f"/system/orgs/{o.get('OrgId')}", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "org": body["OrgName"], "org_id": o.get("OrgId"), "updated": changed}
