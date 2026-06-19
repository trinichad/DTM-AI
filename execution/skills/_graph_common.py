"""Shared plumbing for the Graph-based skills (D-56; SOP: m365-graph).

No NAME attribute → invisible to the registry (I-1). Same contract as _exo_common:
every write verifies itself by re-reading, and a 403 names the exact delegated scope
the owner must add to M365_SCOPES (then re-sign-in the client).
"""
from __future__ import annotations

import re
from typing import Any, Optional

GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def err403(e, doing: str, scope: str) -> dict:
    if e.status == 403:
        return {"ok": False, "error":
                f"Graph refused (403) while {doing} — the sign-in lacks the '{scope}' "
                f"delegated scope. Add it to M365_SCOPES on the M365 card and sign the "
                f"client in again."}
    return {"ok": False, "error": f"Graph HTTP {e.status} while {doing}: {e.body[:300]}"}


def rows(data: Any) -> list[dict]:
    """Unwrap a Graph collection ({'value': [...]}) into a list of dicts."""
    v = data.get("value") if isinstance(data, dict) else data
    return [x for x in (v or []) if isinstance(x, dict)]


def fail(data: Any) -> Optional[dict]:
    """The {'error': ...} envelope from scoped_read/scoped_write, normalized — or None."""
    if isinstance(data, dict) and data.get("error"):
        e = data["error"]
        msg = e.get("message") if isinstance(e, dict) else str(e)
        return {"ok": False, "error": str(msg)}
    return None


def resolve_group(ctx, ident: str) -> tuple[Optional[dict], Optional[dict]]:
    """Resolve an Entra group by id, displayName, or mailNickname.
    Returns (group, None) or (None, error_dict)."""
    from ..clients.scopes import scoped_read
    ident = (ident or "").strip()
    if not ident:
        return None, {"ok": False, "error": "no group given"}
    sel = "id,displayName,mailNickname,securityEnabled,mailEnabled,groupTypes,membershipRule"
    if GUID.match(ident):
        g = scoped_read(ctx, "m365", f"/groups/{ident}", {"$select": sel})
        bad = fail(g)
        if bad:
            return None, bad
        return (g, None) if isinstance(g, dict) and g.get("id") else \
            (None, {"ok": False, "error": f"no group with id '{ident}'"})
    safe = ident.replace("'", "''")
    data = scoped_read(ctx, "m365", "/groups",
                       {"$filter": f"displayName eq '{safe}' or mailNickname eq '{safe}'",
                        "$select": sel})
    bad = fail(data)
    if bad:
        return None, bad
    hits = rows(data)
    if not hits:
        return None, {"ok": False, "error": f"no group named '{ident}'"}
    if len(hits) > 1:
        return None, {"ok": False, "error": f"'{ident}' matched {len(hits)} groups — use the "
                                            f"group id"}
    return hits[0], None


def resolve_user_id(ctx, upn: str) -> tuple[Optional[str], Optional[dict]]:
    """A user's object id from their UPN. Returns (id, None) or (None, error_dict)."""
    from ..clients.scopes import scoped_read
    upn = (upn or "").strip()
    if "@" not in upn:
        return None, {"ok": False, "error": f"'{upn}' is not a sign-in address"}
    u = scoped_read(ctx, "m365", f"/users/{upn}", {"$select": "id,userPrincipalName"})
    bad = fail(u)
    if bad:
        return None, bad
    if not (isinstance(u, dict) and u.get("id")):
        return None, {"ok": False, "error": f"no user '{upn}' found in this client"}
    return str(u["id"]), None


def is_group_member(ctx, gid: str, uid: str) -> bool:
    """Is `uid` a DIRECT member of group `gid`? Uses a TARGETED filtered query
    (ConsistencyLevel: eventual is always sent), so it's correct regardless of group size —
    no false negative on groups with >999 members (D-67). Falls back to a paged scan only if
    the directory rejects the filter."""
    from ..clients.scopes import scoped_read
    safe = str(uid).replace("'", "''")
    data = scoped_read(ctx, "m365", f"/groups/{gid}/members",
                       {"$filter": f"id eq '{safe}'", "$count": "true", "$select": "id"})
    if not fail(data):
        return any(str(m.get("id")) == uid for m in rows(data))
    # fallback: page the member list (older behaviour) if $filter isn't honored
    scan = scoped_read(ctx, "m365", f"/groups/{gid}/members", {"$select": "id", "$top": 999})
    return any(str(m.get("id")) == uid for m in rows(scan))


def find_autopilot_by_serial(ctx, serial: str):
    """Find ONE Autopilot device by exact serial. The Graph $filter contains() can 400 on
    serials with spaces/hyphens (D-67), so on filter failure we fall back to a paged client-
    side scan. Returns (device_dict, None) | (None, None=not found) | (None, error_dict)."""
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read
    base = "/deviceManagement/windowsAutopilotDeviceIdentities"
    serial = (serial or "").strip()
    hits = []
    try:
        q = serial.replace("'", "''")
        data = scoped_read(ctx, "m365", base, {"$filter": f"contains(serialNumber,'{q}')"})
        if fail(data):
            raise _FilterUnsupported()
        hits = [d for d in rows(data) if str(d.get("serialNumber")) == serial]
    except (HttpError, _FilterUnsupported):
        # spaced/odd serial or unsupported filter → scan pages and match exactly client-side
        params = {"$top": 500}
        for _ in range(20):                       # up to ~10k devices, then give up paging
            page = scoped_read(ctx, "m365", base, params)
            bad = fail(page)
            if bad:
                return None, bad
            hits = [d for d in rows(page) if str(d.get("serialNumber")) == serial]
            if hits or not (isinstance(page, dict) and page.get("@odata.nextLink")):
                break
            params = {"$skiptoken": _skiptoken(page.get("@odata.nextLink"))}
    if not hits:
        return None, None
    if len(hits) > 1:
        return None, {"ok": False, "error": f"'{serial}' matched {len(hits)} devices — "
                                            f"give the full serial"}
    return hits[0], None


class _FilterUnsupported(Exception):
    pass


def _skiptoken(nextlink: str) -> str:
    """Pull $skiptoken out of an @odata.nextLink so the m365 client (which takes a '/path')
    can request the next page."""
    import urllib.parse as _u
    qs = _u.parse_qs(_u.urlparse(str(nextlink or "")).query)
    return (qs.get("$skiptoken") or qs.get("%24skiptoken") or [""])[0]
