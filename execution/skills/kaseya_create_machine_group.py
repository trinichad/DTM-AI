"""Create a Kaseya machine group under an organization (D-69; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_create_machine_group"
DESCRIPTION = ("Create a MACHINE GROUP under a Kaseya organization. Give the org (name or "
               "OrgId) and the new machine-group name. Optionally nest it under a parent "
               "machine group by id.")
SOURCE = "kaseya"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "org": {"type": "string", "description": "organization name or OrgId"},
        "name": {"type": "string", "description": "the new machine group's name"},
        "parent_group_id": {"type": "string",
                            "description": "parent machine group id to nest under (optional)"},
    },
    "required": ["org", "name"],
    "additionalProperties": False,
}


def run(ctx, org: str, name: str, parent_group_id: str = "", **_: Any):
    from . import _kaseya_common as k
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "the machine group needs a name"}
    client = ctx.client("kaseya")
    data, e = k.result(client, "/system/orgs")
    if e:
        return {"ok": False, "error": e}
    n = str(org).strip().lower()
    o = next((x for x in k.rows(data)
              if str(x.get("OrgId")) == str(org).strip()
              or str(x.get("OrgName") or "").lower() == n
              or str(x.get("OrgRef") or "").lower() == n), None)
    if not o:
        return {"ok": False, "error": f"no Kaseya org matched '{org}'"}
    oid = o.get("OrgId")
    body: dict[str, Any] = {"MachineGroupName": name, "OrgId": oid}
    if str(parent_group_id).strip():
        body["ParentMachineGroupId"] = str(parent_group_id).strip()
    r = client.write("POST", f"/system/orgs/{oid}/machinegroups", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "created_machine_group": name, "org": o.get("OrgName"), "org_id": oid}
