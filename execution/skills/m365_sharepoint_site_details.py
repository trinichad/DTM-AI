"""One SharePoint site's type, storage, and members (D-56; SOP: m365-graph).

Type detection (the drive-owner trick): a site's default document library DRIVE is owned by
a GROUP exactly when the site is group-connected — i.e. a TEAM site; its members are the
group's members. No owning group ⇒ a communication / standalone document site.
"""
from __future__ import annotations

from typing import Any

NAME = "m365_sharepoint_site_details"
DESCRIPTION = ("Show ONE SharePoint site's details: whether it's a TEAM site (group-connected, "
               "with members) or a communication/document site, its storage used, and its "
               "members. Pass the site's name, URL, or id (find them with "
               "m365_list_sharepoint_sites).")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "site": {"type": "string", "description": "site name, full URL, or site id"},
    },
    "required": ["site"],
    "additionalProperties": False,
}


def _gb(n: Any) -> Any:
    try:
        return round(int(n) / (1024 ** 3), 2)
    except (TypeError, ValueError):
        return None


def _find_site(ctx, ident: str):
    from ..clients.scopes import scoped_read
    from . import _graph_common as g
    ident = (ident or "").strip()
    if "," in ident:                                   # a Graph composite site id
        s = scoped_read(ctx, "m365", f"/sites/{ident}")
        return (s, None) if isinstance(s, dict) and s.get("id") else \
            (None, {"ok": False, "error": f"no site with id '{ident}'"})
    q = ident.rstrip("/").split("/")[-1] if "://" in ident else ident
    data = scoped_read(ctx, "m365", "/sites", {"search": q})
    bad = g.fail(data)
    if bad:
        return None, bad
    hits = g.rows(data)
    if "://" in ident:
        hits = [s for s in hits
                if str(s.get("webUrl") or "").rstrip("/").lower() == ident.rstrip("/").lower()]
    if not hits:
        return None, {"ok": False, "error": f"no SharePoint site matching '{ident}'"}
    if len(hits) > 1:
        names = [f"{s.get('displayName')} ({s.get('webUrl')})" for s in hits[:5]]
        return None, {"ok": False, "error": f"'{ident}' matched {len(hits)} sites — be more "
                                            f"specific: {'; '.join(names)}"}
    return hits[0], None


def run(ctx, site: str, **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read
    from . import _graph_common as g
    try:
        s, bad = _find_site(ctx, site)
        if bad:
            return bad
        sid = str(s.get("id"))
        out: dict[str, Any] = {"ok": True, "name": s.get("displayName") or s.get("name"),
                               "url": s.get("webUrl"), "id": sid,
                               "created": s.get("createdDateTime")}

        drive = scoped_read(ctx, "m365", f"/sites/{sid}/drive",
                            {"$select": "quota,owner,name"})
        if not g.fail(drive) and isinstance(drive, dict):
            quota = drive.get("quota") or {}
            out["storage"] = {"used_gb": _gb(quota.get("used")),
                              "total_gb": _gb(quota.get("total")),
                              "remaining_gb": _gb(quota.get("remaining"))}
            owner_group = (drive.get("owner") or {}).get("group") or {}
            gid = owner_group.get("id")
        else:
            gid = None
            out["storage"] = "unavailable"

        if gid:
            out["type"] = "team site (Microsoft 365 group-connected)"
            out["group"] = {"name": owner_group.get("displayName"), "id": gid}
            members = scoped_read(ctx, "m365", f"/groups/{gid}/members",
                                  {"$select": "displayName,userPrincipalName", "$top": 999})
            if not g.fail(members):
                out["members"] = [{"name": m.get("displayName"),
                                   "user": m.get("userPrincipalName")}
                                  for m in g.rows(members)]
                out["member_count"] = len(out["members"])
        else:
            out["type"] = "communication / standalone document site (no owning group)"
            out["note"] = ("permissions on non-group sites are managed in SharePoint itself "
                           "and aren't exposed by this read")
        return out
    except HttpError as exc:
        return g.err403(exc, "reading the site", "Sites.Read.All")
