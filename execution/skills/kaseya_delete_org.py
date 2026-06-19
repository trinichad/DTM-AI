"""Delete a Kaseya organization — DESTRUCTIVE, catastrophic (D-69; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_delete_org"
DESCRIPTION = ("DELETE an entire ORGANIZATION (client) from Kaseya — removes the org and its "
               "machine groups / asset associations. This is catastrophic and rarely correct; "
               "double-check the org name with the user first. Requires the exact OrgRef code "
               "as a confirmation. Every run needs fresh owner approval (cannot be disabled).")
SOURCE = "kaseya"
CATEGORY = "destructive"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "org": {"type": "string", "description": "organization name or OrgId to delete"},
        "confirm_org_ref": {"type": "string",
                            "description": "the org's exact OrgRef code, as a safety "
                                           "confirmation that this is the right org"},
    },
    "required": ["org", "confirm_org_ref"],
    "additionalProperties": False,
}


def run(ctx, org: str, confirm_org_ref: str, **_: Any):
    from . import _kaseya_common as k
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
    # the confirmation code must match THIS org's ref — guards against deleting the wrong client
    if str(confirm_org_ref).strip().lower() != str(o.get("OrgRef") or "").lower():
        return {"ok": False, "error": f"confirm_org_ref '{confirm_org_ref}' does not match "
                                      f"org '{o.get('OrgName')}' (ref '{o.get('OrgRef')}') — "
                                      f"refusing to delete"}
    oid = o.get("OrgId")
    r = client.write_destructive("DELETE", f"/system/orgs/{oid}")
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "deleted_org": o.get("OrgName"), "org_ref": o.get("OrgRef"),
            "org_id": oid}
