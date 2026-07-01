"""Status / estimate of a Content Search (D-115; SOP: exchange-online).

Get-ComplianceSearch for one search (by name) or ALL of them. Reports Status, the estimated item
count and total size, the query and locations, and a per-mailbox breakdown parsed from
SuccessResults. Headline item/size come from real object properties, not string parsing.
"""
from __future__ import annotations

from typing import Any

NAME = "exo_content_search_status"
DESCRIPTION = (
    "Show the status and estimate of a Microsoft Purview Content Search in THIS client. Pass `name` "
    "for one search — returns its Status (NotStarted/InProgress/Completed/Failed), estimated item "
    "count and total size, the query, the searched mailboxes, and a per-mailbox item/size breakdown. "
    "Omit `name` to LIST every content search with its status. Pair with exo_content_search_create "
    "and exo_content_search_preview.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string",
                 "description": "the search name; omit to list ALL content searches"},
    },
    "required": [],
    "additionalProperties": False,
}


def run(ctx, name: str = "", **_: Any):
    from . import _content_search as cs
    name = (name or "").strip()
    exo = ctx.client("exo")

    r = exo.invoke_compliance("Get-ComplianceSearch", {"Identity": name} if name else {})
    e = _err(r)
    if e:
        if name and ("not found" in e.lower() or "couldn't be found" in e.lower()):
            return {"ok": False, "error": f"no content search named '{name}' — create one with "
                                          f"exo_content_search_create"}
        return {"ok": False, "error": e + cs.role_hint(e)}

    found = cs.rows(r)
    if name:
        if not found:
            return {"ok": False, "error": f"no content search named '{name}'"}
        return {"ok": True, **_summarize(found[0], cs)}
    # list mode — one compact row per search, newest-looking first left as the API returns them
    return {"ok": True, "count": len(found),
            "searches": [{"name": s.get("Name"), "status": s.get("Status"),
                          "items": cs._int(s.get("Items")),
                          "size": cs.human_size(cs._int(s.get("Size")))}
                         for s in found]}


def _summarize(s: dict, cs) -> dict:
    items = cs._int(s.get("Items"))
    size_bytes = cs._int(s.get("Size"))
    return {"name": s.get("Name"), "status": s.get("Status"),
            "items": items, "size_bytes": size_bytes, "size": cs.human_size(size_bytes),
            "query": s.get("ContentMatchQuery"),
            "locations": s.get("ExchangeLocation"),
            "by_mailbox": cs.parse_location_stats(s.get("SuccessResults")),
            "errors": s.get("Errors") or None}


def _err(r: Any) -> str:
    return str(r.get("error")) if isinstance(r, dict) and r.get("error") else ""
