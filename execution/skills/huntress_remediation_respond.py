"""Approve or reject the proposed remediations on a Huntress incident report (D-82)."""
from __future__ import annotations

import re
from typing import Any

NAME = "huntress_remediation_respond"
DESCRIPTION = ("Respond to the remediations Huntress proposed on an incident report: "
               "`action`='approve' (let Huntress carry them out) or 'reject'. Give the "
               "`incident_id`; optionally pass specific `remediation_ids` (otherwise all proposed "
               "are acted on). Review with huntress_get_incident first.")
SOURCE = "huntress"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_ACTIONS = {"approve": "bulk_approval", "reject": "bulk_rejection"}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "incident_id": {"type": "string", "description": "the Huntress incident report id"},
        "action": {"type": "string", "enum": list(_ACTIONS), "description": "approve or reject"},
        "remediation_ids": {"type": "array", "items": {"type": "string"},
                            "description": "specific remediation ids (optional; default all proposed)"},
    },
    "required": ["incident_id", "action"],
    "additionalProperties": False,
}


def run(ctx, incident_id: str, action: str, remediation_ids: Any = None, **_: Any):
    iid = str(incident_id or "").strip()
    verb = _ACTIONS.get((action or "").strip().lower())
    if not re.match(r"^\d+$", iid):
        return {"ok": False, "error": "incident_id must be numeric"}
    if not verb:
        return {"ok": False, "error": "action must be 'approve' or 'reject'"}
    client = ctx.client("huntress")
    acct = client.get("/account") or {}
    account_id = acct.get("id") if isinstance(acct, dict) else None
    if not account_id:
        return {"ok": False, "error": "could not determine the Huntress account id from /account"}
    body: dict[str, Any] = {}
    if isinstance(remediation_ids, list) and remediation_ids:
        body["remediation_ids"] = [str(x).strip() for x in remediation_ids if str(x or "").strip()]
    path = f"/accounts/{account_id}/incident_reports/{iid}/remediations/{verb}"
    r = client.write("POST", path, body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "incident_id": iid, "action": action, "result": r,
            "note": f"remediations {action}d"}
