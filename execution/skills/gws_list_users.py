"""List / SEARCH Google Workspace users via the Admin SDK Directory API (D-118) — read-only."""
from __future__ import annotations

from typing import Any, Callable, Optional

NAME = "gws_list_users"
DESCRIPTION = ("List or search Google Workspace users (name, primary email, suspended state, admin "
               "flag, org unit). Use `search` to pass a Google Directory query (e.g. "
               "\"email:jsmith*\", \"name:'Jane Smith'\", \"isAdmin=true\", \"orgUnitPath=/Sales\") "
               "which returns MANY users in ONE call — do NOT call this once per person. Use "
               "`name_contains` to find every user whose name or email CONTAINS a substring anywhere "
               "(case-insensitive) — good for naming conventions. Scoped to the selected client; when "
               "the session is 'All clients' (*) it aggregates across every signed-in Google "
               "Workspace client and tags each user with their `tenant`.")
SOURCE = "gws"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True

_USERS_PATH = "/admin/directory/v1/users"
_CONTAINS_FIELDS = ("primaryEmail", "fullName")
_MAX_SCAN_PAGES = 40                 # 40 × 500 = 20k users — far beyond any MSP client; bounds a scan
_PAGE = 500                          # Directory API maxResults cap


PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "search": {"type": "string",
                   "description": "a Google Directory API query, e.g. \"email:jsmith*\", "
                                  "\"name:'Jane Smith'\", \"isAdmin=true\", \"suspended=true\", or "
                                  "\"orgUnitPath=/Sales\" — returns the whole matching set in one call"},
        "name_contains": {"type": "string",
                          "description": "find every user whose full name or primary email contains "
                                         "this text ANYWHERE (case-insensitive substring) — use for "
                                         "naming conventions; returns the complete matching set"},
        "top": {"type": "integer", "description": "max users to return (1–500, default 200)"},
    },
    "additionalProperties": False,
}


def _base_params(query: str, top: int) -> dict[str, Any]:
    params: dict[str, Any] = {"customer": "my_customer", "maxResults": top,
                              "projection": "basic", "orderBy": "email"}
    if (query or "").strip():
        params["query"] = query.strip()
    return params


def _slim(u: dict) -> dict:
    """Compact a Directory user for the model + UI: the fields a listing/table needs, drop the
    opaque id and empties. Downstream tools resolve users by primary email, so dropping id is free."""
    name = (u.get("name") or {}) if isinstance(u.get("name"), dict) else {}
    out: dict[str, Any] = {
        "primaryEmail": u.get("primaryEmail"),
        "fullName": name.get("fullName"),
        "suspended": bool(u.get("suspended")),
    }
    if u.get("isAdmin"):
        out["isAdmin"] = True
    for k in ("orgUnitPath", "tenant"):
        if u.get(k):
            out[k] = u[k]
    return out


def _fullname(u: dict) -> str:
    name = (u.get("name") or {}) if isinstance(u.get("name"), dict) else {}
    return str(name.get("fullName") or "")


def _scan(get_page: Callable[[dict], Any], base: dict) -> tuple[list, Optional[dict], bool]:
    """Follow Directory nextPageToken, collecting users up to _MAX_SCAN_PAGES.
    Returns (users, error_or_None, truncated)."""
    users: list = []
    params = dict(base)
    for _ in range(_MAX_SCAN_PAGES):
        data = get_page(params)
        if isinstance(data, dict) and data.get("error"):
            return users, data, False
        page = (data.get("users") if isinstance(data, dict) else data) or []
        users.extend(u for u in page if isinstance(u, dict))
        nxt = data.get("nextPageToken") if isinstance(data, dict) else None
        if not nxt:
            return users, None, False
        params = {**base, "pageToken": nxt}
    return users, None, True                       # hit the page cap — more may exist


def _matches(u: dict, needle: str) -> bool:
    hay = (str(u.get("primaryEmail") or "") + " " + _fullname(u)).lower()
    return needle in hay


def _connected_or_error(ctx):
    """(cfg, connected_list) for an all-clients read, or ({'error':...}, None)."""
    from execution.core import gws_auth
    from execution.core.config import get_config
    from execution.clients.scopes import is_allowed_read
    cfg = get_config()
    connected = gws_auth.list_connected(cfg)
    if not connected:
        return {"error": "no Google Workspace client is signed in yet — connect one on the "
                         "Google Workspace card"}, None
    if ctx.client_factory is None:
        return {"error": "no client factory available for a cross-client read"}, None
    ok, reason = is_allowed_read("gws", _USERS_PATH)
    if not ok:
        return {"error": reason}, None
    return cfg, connected


def _one_tenant(ctx, query: str, top: int, needle: str) -> dict:
    from execution.clients.scopes import scoped_read
    base = _base_params(query, _PAGE if needle else top)
    users, err, trunc = _scan(lambda p: scoped_read(ctx, "gws", _USERS_PATH, p), base)
    if err:
        return err
    if needle:
        users = [u for u in users if _matches(u, needle)]
    slimmed = [_slim(u) for u in users]
    out: dict[str, Any] = {"count": len(slimmed), "users": slimmed}
    if query:
        out["searched_for"] = query
    if needle:
        out["match"] = "contains"
        out["searched_for"] = needle
    if trunc:
        out["note"] = (f"scanned the first {_MAX_SCAN_PAGES * _PAGE} users (page cap) — there may "
                       f"be more; narrow with `search`")
    return out


def _all_clients(ctx, query: str, top: int, needle: str) -> dict:
    cfg, connected = _connected_or_error(ctx)
    if connected is None:
        return cfg                                 # the error dict
    base = _base_params(query, _PAGE if needle else top)
    by_client, all_users, errors, truncated = [], [], [], []
    for t in connected:
        client = ctx.client_factory("gws", t)
        users, err, trunc = _scan(lambda p: client.get(_USERS_PATH, p), base)
        if err:
            errors.append({"tenant": t, "error": str(err.get("error"))[:160]}); continue
        if needle:
            users = [u for u in users if _matches(u, needle)]
        all_users.extend(_slim({**u, "tenant": t}) for u in users)
        by_client.append({"tenant": t, "count": len(users)})
        if trunc:
            truncated.append(t)
    out: dict[str, Any] = {"scope": "all_clients", "clients_searched": connected,
                           "count": len(all_users), "by_client": by_client, "users": all_users}
    if query:
        out["searched_for"] = query
    if needle:
        out["match"] = "contains"; out["searched_for"] = needle
    if errors:
        out["errors"] = errors
    if truncated:
        out["note"] = ("page cap hit for: " + ", ".join(truncated) + " — matches for those "
                       "clients may be incomplete")
    return out


def run(ctx, search: str = "", name_contains: str = "", top: int = 200, **_: Any):
    try:
        top = max(1, min(int(top), _PAGE))
    except (TypeError, ValueError):
        top = 200
    needle = (name_contains or "").strip().lower()
    query = (search or "").strip()
    is_star = (getattr(ctx, "tenant_id", "") or "") == "*"
    return (_all_clients(ctx, query, top, needle) if is_star
            else _one_tenant(ctx, query, top, needle))
