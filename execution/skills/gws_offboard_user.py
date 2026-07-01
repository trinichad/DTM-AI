"""Offboard a Google Workspace user — the full MSP sequence (D-118).

Reversible-first: SUSPEND (never delete) + reset password + remove from groups + remove license, and
optionally transfer the user's Drive/Docs to their manager (Data Transfer API). A composite the agent
calls once; each step reuses the dedicated tool / API and is reported separately.
"""
from __future__ import annotations

from typing import Any, Optional

# Well-known application id for Drive and Docs in the Admin Data Transfer API.
_DRIVE_APP_ID = "55656082996"

NAME = "gws_offboard_user"
DESCRIPTION = (
    "OFFBOARD a Google Workspace user (reversible — SUSPENDS, never deletes). Steps, in order: "
    "suspend sign-in -> reset the password to a random value -> remove from all groups -> remove a "
    "license (if remove_license_sku given) -> transfer Drive/Docs ownership to a manager (if "
    "transfer_drive_to given). Pass the departing user; optionally the manager's email to receive "
    "their Drive files, and the license SKU to free. Reports each step.")
SOURCE = "gws"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the departing user's email"},
        "transfer_drive_to": {"type": "string",
                              "description": "manager's email to receive the user's Drive/Docs (optional)"},
        "remove_license_sku": {"type": "string",
                               "description": "license SKU to free (optional; see gws_assign_license)"},
        "reset_password": {"type": "boolean", "description": "reset the password (default true)"},
        "remove_from_groups": {"type": "boolean", "description": "remove from all groups (default true)"},
    },
    "required": ["user"],
    "additionalProperties": False,
}


def _user_id(ctx, email: str) -> Optional[str]:
    from ..clients.scopes import scoped_read
    u = scoped_read(ctx, "gws", f"/admin/directory/v1/users/{email}", {"projection": "basic"})
    return u.get("id") if isinstance(u, dict) else None


def run(ctx, user: str = "", transfer_drive_to: str = "", remove_license_sku: str = "",
        reset_password: bool = True, remove_from_groups: bool = True, **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read, scoped_write
    from ._gws_write import err_msg, api_error
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a valid sign-in address"}
    steps: dict[str, Any] = {}
    all_ok = True

    def _and(ok):
        nonlocal all_ok
        all_ok &= bool(ok)

    # 1) suspend
    from .gws_suspend_user import run as suspend
    r = suspend(ctx, user=user)
    steps["suspend"] = "done" if r.get("ok") else r.get("error")
    _and(r.get("ok"))
    if not r.get("ok"):                              # if we can't even suspend, stop
        return {"ok": False, "user": user, "steps": steps,
                "error": "could not suspend the user — aborting offboarding"}

    # 2) reset password (locks the account further; suspend already blocks sign-in)
    if reset_password:
        from .gws_reset_password import run as reset
        r = reset(ctx, user=user, must_change=True)
        steps["reset_password"] = "done" if r.get("ok") else r.get("error")
        _and(r.get("ok"))

    # 3) remove from all groups
    if remove_from_groups:
        from .gws_remove_group_member import run as remove_member
        grps = scoped_read(ctx, "gws", "/admin/directory/v1/groups",
                           {"userKey": user, "maxResults": 200})
        if isinstance(grps, dict) and grps.get("error"):
            steps["groups"] = f"could not list groups: {grps['error']}"
            _and(False)
        else:
            rows = (grps.get("groups") if isinstance(grps, dict) else None) or []
            res: dict[str, Any] = {}
            for g in rows:
                email = g.get("email") if isinstance(g, dict) else None
                if not email:
                    continue
                rr = remove_member(ctx, group=email, member=user)
                res[email] = "removed" if rr.get("ok") else rr.get("error")
                _and(rr.get("ok"))
            steps["groups"] = res or "none"

    # 4) remove license
    if (remove_license_sku or "").strip():
        from .gws_remove_license import run as remove_license
        r = remove_license(ctx, user=user, sku=remove_license_sku.strip())
        steps["license"] = "removed" if r.get("ok") else r.get("error")
        _and(r.get("ok"))

    # 5) transfer Drive/Docs to the manager (Data Transfer API — needs numeric user ids)
    if (transfer_drive_to or "").strip():
        mgr = transfer_drive_to.strip()
        old_id, new_id = _user_id(ctx, user), _user_id(ctx, mgr)
        if not (old_id and new_id):
            steps["drive_transfer"] = f"could not resolve user ids (user={bool(old_id)}, manager={bool(new_id)})"
            _and(False)
        else:
            body = {"oldOwnerUserId": old_id, "newOwnerUserId": new_id,
                    "applicationDataTransfers": [{"applicationId": _DRIVE_APP_ID}]}
            try:
                tr = scoped_write(ctx, "gws", "/admin/datatransfer/v1/transfers",
                                  body=body, method="POST")
                blocked = api_error(tr)
                if blocked:
                    steps["drive_transfer"] = blocked; _and(False)
                else:
                    steps["drive_transfer"] = f"started (to {mgr})"
            except HttpError as e:
                steps["drive_transfer"] = err_msg(e); _and(False)

    return {"ok": all_ok, "user": user, "steps": steps,
            "summary": ("offboarding complete — account suspended"
                        + (", Drive transfer started" if steps.get("drive_transfer", "").startswith("started") else "")
                        if all_ok else "offboarding ran with some failures — see steps")}
