"""List the client's SharePoint sites (D-56; SOP: m365-graph)."""
from __future__ import annotations

from typing import Any

NAME = "m365_list_sharepoint_sites"
DESCRIPTION = ("List the client's SHAREPOINT SITES (name, URL, created/modified). Use "
               "`search` to find specific sites by name. For one site's type, size, and "
               "members use m365_sharepoint_site_details.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "search": {"type": "string", "description": "site name contains this text (optional "
                                                    "— empty lists all sites)"},
        "limit": {"type": "integer", "description": "max results (default 100, max 500)"},
    },
    "additionalProperties": False,
}


def run(ctx, search: str = "", limit: int = 100, **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read
    from . import _graph_common as g
    limit = max(1, min(int(limit or 100), 500))
    q = (search or "").strip() or "*"
    try:
        data = scoped_read(ctx, "m365", "/sites", {"search": q, "$top": limit})
    except HttpError as exc:
        return g.err403(exc, "listing SharePoint sites", "Sites.Read.All")
    bad = g.fail(data)
    if bad:
        return bad
    sites = [{"name": s.get("displayName") or s.get("name"), "id": s.get("id"),
              "url": s.get("webUrl"), "created": s.get("createdDateTime"),
              "modified": s.get("lastModifiedDateTime")}
             for s in g.rows(data)
             if "personal" not in str(s.get("webUrl") or "")]   # skip OneDrive personal sites
    out: dict[str, Any] = {"count": len(sites), "sites": sites}
    if (search or "").strip():
        out["searched_for"] = search.strip()
    note = "use m365_sharepoint_site_details for a site's type, size, and members"
    if isinstance(data, dict) and data.get("@odata.nextLink"):
        note = ("more sites exist beyond this page — narrow with `search` or raise `limit` "
                "(max 500). ") + note
    out["note"] = note
    return out
