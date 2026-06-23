"""Delete a directory (Entra) group (D-65; SOP: m365-graph). The opposite of m365_create_group."""
from __future__ import annotations

from typing import Any

NAME = "m365_delete_group"
DESCRIPTION = ("DELETE a DIRECTORY (Entra) group — security, Microsoft 365, or dynamic. "
               "WARNING: deleting a Microsoft 365 group also removes its SharePoint site, "
               "shared mailbox, and Teams team (Microsoft keeps them recoverable for ~30 "
               "days). Verifies the group is gone before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "group": {"type": "string", "description": "group name or id"},
    },
    "required": ["group"],
    "additionalProperties": False,
}


def run(ctx, group: str, **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_delete, scoped_read
    from . import _graph_common as g
    try:
        grp, bad = g.resolve_group(ctx, group)
        if bad:
            return bad
        gid = str(grp.get("id"))
        is_m365 = "Unified" in [str(t) for t in (grp.get("groupTypes") or [])]
        r = scoped_delete(ctx, "m365", f"/groups/{gid}")
        bad = g.fail(r)
        if bad:
            return bad

        # A deleted group can stay readable for a few seconds (propagation) — poll until it's gone
        # (404) rather than failing on the first stale read (D-104).
        def _read_group():
            try:
                return scoped_read(ctx, "m365", f"/groups/{gid}", {"$select": "id"})
            except HttpError as exc:
                if exc.status == 404:
                    return {"error": "gone"}               # 404 = deleted = success
                raise
        _ok, check = g.settle(_read_group,
                              lambda c: not (isinstance(c, dict) and c.get("id")))
    except HttpError as exc:
        return g.err403(exc, "deleting the group", "Group.ReadWrite.All")
    if isinstance(check, dict) and check.get("id"):
        return {"ok": False, "step": "verify", "pending": True,
                "error": "the delete returned but the group is still readable after a short poll "
                         "— usually propagation lag; re-check in Entra shortly"}
    return {"ok": True, "deleted": grp.get("displayName"), "id": gid,
            "note": ("the Microsoft 365 group, its SharePoint site and group mailbox are "
                     "recoverable for ~30 days" if is_m365
                     else "the security group was removed")}
