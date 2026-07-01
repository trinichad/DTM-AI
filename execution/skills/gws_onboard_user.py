"""Onboard / set up a Google Workspace user — the full MSP sequence (D-118).

A composite the agent calls once, after gathering the spec with the owner. Each step reuses the
dedicated tool and is reported separately. Approved once (as this write tool); the steps then run.
"""
from __future__ import annotations

from typing import Any, Optional

NAME = "gws_onboard_user"
DESCRIPTION = (
    "ONBOARD / set up a Google Workspace user in order. BEFORE calling, gather the spec with the "
    "owner: (1) if the account doesn't exist yet, first_name + last_name to create it; (2) the "
    "license SKU to assign (see gws_assign_license for common SKUs); (3) the org unit; (4) the "
    "groups to join; (5) any Shared Drives to grant. Then call this ONCE. Steps: create (only if "
    "missing) -> assign license -> move org unit -> add to groups -> grant Shared Drive access. "
    "Reports each step's outcome.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's email (sign-in)"},
        "create_first_name": {"type": "string", "description": "first name — only used to CREATE if missing"},
        "create_last_name": {"type": "string", "description": "last name — only used to CREATE if missing"},
        "password": {"type": "string", "description": "initial password if creating (optional — generated)"},
        "license_sku": {"type": "string", "description": "license SKU to assign (optional)"},
        "org_unit_path": {"type": "string", "description": "org unit to place the user in (optional)"},
        "groups": {"type": "array", "description": "groups to add the user to", "items": {
            "type": "object", "properties": {
                "group": {"type": "string", "description": "group email"},
                "role": {"type": "string", "enum": ["MEMBER", "MANAGER", "OWNER"]}},
            "required": ["group"]}},
        "shared_drives": {"type": "array", "description": "Shared Drives to grant access to", "items": {
            "type": "object", "properties": {
                "drive_id": {"type": "string"},
                "role": {"type": "string", "enum": ["organizer", "fileOrganizer", "writer",
                                                    "commenter", "reader"]}},
            "required": ["drive_id"]}},
    },
    "required": ["user"],
    "additionalProperties": False,
}


def run(ctx, user: str = "", create_first_name: str = "", create_last_name: str = "",
        password: str = "", license_sku: str = "", org_unit_path: str = "",
        groups: Optional[list] = None, shared_drives: Optional[list] = None, **_: Any):
    from ..clients.scopes import scoped_read
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a valid sign-in address"}
    steps: dict[str, Any] = {}
    all_ok = True

    def _and(ok):
        nonlocal all_ok
        all_ok &= bool(ok)

    # 1) ensure the account exists (create only if missing + names supplied)
    existing = scoped_read(ctx, "gws", f"/admin/directory/v1/users/{user}", {"projection": "basic"})
    exists = isinstance(existing, dict) and bool(existing.get("primaryEmail"))
    if not exists:
        if not (create_first_name.strip() and create_last_name.strip()):
            return {"ok": False, "user": user,
                    "error": f"'{user}' doesn't exist — pass create_first_name and create_last_name "
                             f"to create the account, or use an existing user."}
        from .gws_create_user import run as create_user
        cr = create_user(ctx, email=user, first_name=create_first_name.strip(),
                         last_name=create_last_name.strip(), password=password)
        steps["create_user"] = "done" if cr.get("ok") else cr.get("error")
        if not cr.get("ok"):
            return {"ok": False, "user": user, "steps": steps,
                    "error": "couldn't create the account — aborting (nothing else ran)"}
        if cr.get("initial_password"):
            steps["initial_password"] = cr["initial_password"]

    out: dict[str, Any] = {"ok": True, "user": user, "steps": steps}

    # 2) license
    if (license_sku or "").strip():
        from .gws_assign_license import run as assign
        r = assign(ctx, user=user, sku=license_sku.strip())
        steps["license"] = "done" if r.get("ok") else r.get("error")
        _and(r.get("ok"))

    # 3) org unit
    if (org_unit_path or "").strip():
        from .gws_move_org_unit import run as move_ou
        r = move_ou(ctx, user=user, org_unit_path=org_unit_path.strip())
        steps["org_unit"] = "done" if r.get("ok") else r.get("error")
        _and(r.get("ok"))

    # 4) groups
    if groups:
        from .gws_add_group_member import run as add_member
        res: dict[str, Any] = {}
        for g in groups:
            if not isinstance(g, dict) or not g.get("group"):
                continue
            r = add_member(ctx, group=str(g["group"]), member=user,
                           role=str(g.get("role") or "MEMBER"))
            res[str(g["group"])] = "done" if r.get("ok") else r.get("error")
            _and(r.get("ok"))
        steps["groups"] = res

    # 5) shared drives
    if shared_drives:
        from .gws_add_shared_drive_member import run as add_drive
        res = {}
        for d in shared_drives:
            if not isinstance(d, dict) or not d.get("drive_id"):
                continue
            r = add_drive(ctx, drive_id=str(d["drive_id"]), member=user,
                          role=str(d.get("role") or "writer"))
            res[str(d["drive_id"])] = "done" if r.get("ok") else r.get("error")
            _and(r.get("ok"))
        steps["shared_drives"] = res

    out["ok"] = all_ok
    out["summary"] = "onboarding complete" if all_ok else "onboarding ran with some failures — see steps"
    return out
