"""Create a Kaseya organization (D-69; SOP: kaseya-vsa)."""
from __future__ import annotations

import re
from typing import Any

NAME = "kaseya_create_org"
DESCRIPTION = ("Create a new ORGANIZATION (client) in Kaseya. Give a display name and a short "
               "org reference/id (the unique 'OrgRef' code, letters/digits, e.g. 'acme'). "
               "Verifies the org exists afterwards.")
SOURCE = "kaseya"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "organization display name"},
        "org_ref": {"type": "string",
                    "description": "unique short org reference code (letters/digits/_-, e.g. "
                                   "'acme')"},
    },
    "required": ["name", "org_ref"],
    "additionalProperties": False,
}


def run(ctx, name: str, org_ref: str, **_: Any):
    from . import _kaseya_common as k
    name = (name or "").strip()
    ref = (org_ref or "").strip()
    if not name:
        return {"ok": False, "error": "the org needs a name"}
    if not re.match(r"^[A-Za-z0-9_.-]+$", ref):
        return {"ok": False, "error": "org_ref must be letters/digits/_-. (no spaces)"}
    client = ctx.client("kaseya")
    existing, _e = k.result(client, "/system/orgs")
    if any(str(o.get("OrgRef") or "").lower() == ref.lower() for o in k.rows(existing)):
        return {"ok": False, "error": f"an org with ref '{ref}' already exists"}

    r = client.write("POST", "/system/orgs", {"OrgName": name, "OrgRef": ref})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    after, _e = k.result(client, "/system/orgs")
    made = next((o for o in k.rows(after) if str(o.get("OrgRef") or "").lower() == ref.lower()),
                None)
    if not made:
        return {"ok": False, "step": "verify",
                "error": "the create call returned but the org could not be read back — check "
                         "Kaseya directly"}
    return {"ok": True, "created": name, "org_ref": ref, "org_id": made.get("OrgId")}
