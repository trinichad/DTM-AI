"""Delete a Kaseya machine group — DESTRUCTIVE (D-69; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_delete_machine_group"
DESCRIPTION = ("DELETE a MACHINE GROUP from an organization in Kaseya. The group must be empty "
               "(no agents) in Kaseya's rules. Give the org (name or OrgId) and the machine "
               "group id. Every run needs fresh owner approval (cannot be disabled).")
SOURCE = "kaseya"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "org": {"type": "string", "description": "organization name or OrgId"},
        "machine_group_id": {"type": "string", "description": "the machine group id to delete"},
    },
    "required": ["org", "machine_group_id"],
    "additionalProperties": False,
}


def run(ctx, org: str, machine_group_id: str, **_: Any):
    from . import _kaseya_common as k
    mgid = str(machine_group_id or "").strip()
    if not mgid.isdigit():
        return {"ok": False, "error": "machine_group_id must be the numeric id"}
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
    # VSA 9 deletes a machine group by its own id: DELETE /system/machinegroups/{id} (the org-scoped
    # path is create-only — POST /system/orgs/{oid}/machinegroups). Verified vs the live Swagger.
    r = client.write_destructive("DELETE", f"/system/machinegroups/{mgid}")
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "deleted_machine_group": mgid, "org": o.get("OrgName"), "org_id": oid}
