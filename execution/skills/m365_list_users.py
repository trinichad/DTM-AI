"""List / SEARCH Microsoft 365 / Entra users via Microsoft Graph (D-32) — read-only."""
from __future__ import annotations

from typing import Any, Callable, Optional

NAME = "m365_list_users"
DESCRIPTION = ("List or search Microsoft 365 / Entra users (display name, sign-in name, email, "
               "enabled state, job title, department). To look up MANY specific people at once "
               "(e.g. resolve a list of names to their email addresses) pass `names` (a list) — "
               "this resolves the WHOLE list in ONE call; do NOT call this tool once per person. "
               "Use `search` to find people by a single name/email (Graph keyword search — matches "
               "whole words/prefixes, best for big tenants); use `name_contains` to find every "
               "user whose display name / sign-in / email CONTAINS a substring anywhere "
               "(case-insensitive) — the right tool for naming conventions like a 'zzz_' prefix on "
               "archived accounts, since Graph keyword search can't do substring matches; or "
               "`filter` for an OData query. Scoped to the selected client; when the session is "
               "'All clients' (*) it aggregates across every signed-in M365 client and tags each "
               "user with their `tenant`.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
_SELECT = "id,displayName,userPrincipalName,mail,accountEnabled,jobTitle,department"
_CONTAINS_FIELDS = ("displayName", "userPrincipalName", "mail")
_MAX_SCAN_PAGES = 30                 # ~30k users — far beyond any MSP client; bounds a runaway scan
_NAME_TOP = 25                       # per-name search cap for a batch `names` lookup
_MAX_NAMES = 200                     # bound a single batch request
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "names": {"type": "array", "items": {"type": "string"},
                  "description": "look up MANY people in ONE call — a list of names (or emails). "
                                 "Each is resolved by Graph keyword search and the matches are "
                                 "returned together, each tagged with the `matched_query` that "
                                 "found it; names with no match are listed under `not_found`. Use "
                                 "this instead of calling the tool once per person."},
        "search": {"type": "string",
                   "description": "find users whose name/email/sign-in contains this WORD/prefix "
                                  "(Graph keyword search, e.g. 'smith') — fast on large tenants"},
        "name_contains": {"type": "string",
                          "description": "find every user whose display name, sign-in (UPN), or "
                                         "email contains this text ANYWHERE (case-insensitive "
                                         "substring) — use for naming conventions, e.g. 'zzz_'. "
                                         "Returns the complete matching set in one call."},
        "filter": {"type": "string",
                   "description": "optional OData $filter, e.g. accountEnabled eq false"},
        "top": {"type": "integer", "description": "max users to return (1–999, default 200)"},
    },
    "additionalProperties": False,
}


def _params(search: str, filter: str, top: int) -> dict[str, Any]:
    params: dict[str, Any] = {"$top": top, "$select": _SELECT, "$count": "true"}
    q = (search or "").strip().replace('"', "")
    if q:                                          # Graph $search across the useful fields
        params["$search"] = (f'"displayName:{q}" OR "mail:{q}" OR "userPrincipalName:{q}" '
                             f'OR "givenName:{q}" OR "surname:{q}"')
    elif (filter or "").strip():
        params["$filter"] = filter.strip()
    return params


def _skiptoken(next_link: str) -> str:
    from urllib.parse import urlparse, parse_qs
    try:
        return (parse_qs(urlparse(next_link).query).get("$skiptoken") or [""])[0]
    except Exception:                              # malformed nextLink — stop paging, no crash
        return ""


def _scan(get_page: Callable[[dict], Any], base: dict) -> tuple[list, Optional[dict], bool]:
    """Follow Graph @odata.nextLink, collecting users up to _MAX_SCAN_PAGES. `get_page(params)`
    returns one Graph response. Returns (users, error_or_None, truncated)."""
    users: list = []
    params = dict(base)
    for _ in range(_MAX_SCAN_PAGES):
        data = get_page(params)
        if isinstance(data, dict) and data.get("error"):
            return users, data, False
        page = (data.get("value") if isinstance(data, dict) else data) or []
        users.extend(u for u in page if isinstance(u, dict))
        nxt = data.get("@odata.nextLink") if isinstance(data, dict) else None
        if not nxt:
            return users, None, False
        tok = _skiptoken(nxt)
        if not tok:
            return users, None, True
        params = {**base, "$skiptoken": tok}
    return users, None, True                       # hit the page cap — more may exist


def _matches(u: dict, needle: str) -> bool:
    return any(needle in str(u.get(f) or "").lower() for f in _CONTAINS_FIELDS)


def _slim(u: dict) -> dict:
    """Compact a user for the model + UI: keep the fields a listing/table needs, drop the GUID
    `id` and empty values, and omit `mail` when it just echoes the UPN. This is what lets a large
    match set (e.g. 140 'zzz_' users) fit the model's context budget in ONE result instead of
    being truncated — which previously made the agent re-call and loop to the round cap (D-94).
    Downstream tools resolve users by UPN/email, so dropping the raw id costs nothing here."""
    upn = u.get("userPrincipalName") or ""
    out: dict[str, Any] = {"displayName": u.get("displayName"), "userPrincipalName": upn,
                           "accountEnabled": u.get("accountEnabled")}
    mail = u.get("mail")
    if mail and str(mail).lower() != str(upn).lower():
        out["mail"] = mail
    for k in ("jobTitle", "department", "tenant"):
        if u.get(k):
            out[k] = u[k]
    return out


def _contains_one_tenant(ctx, needle: str) -> dict:
    from execution.clients.scopes import scoped_read
    base = {"$top": 999, "$select": _SELECT, "$count": "true"}
    users, err, trunc = _scan(lambda p: scoped_read(ctx, "m365", "/users", p), base)
    if err:
        return err
    matched = [_slim(u) for u in users if _matches(u, needle)]
    out: dict[str, Any] = {"count": len(matched), "users": matched,
                           "searched_for": needle, "match": "contains", "scanned": len(users)}
    if trunc:
        out["note"] = (f"scanned the first {len(users)} users (page cap) — there may be more "
                       f"unscanned; this match set is complete only up to that point")
    return out


def _contains_all_clients(ctx, needle: str) -> dict:
    from execution.core import m365_auth
    from execution.core.config import get_config
    from execution.clients.scopes import is_allowed_read
    cfg = get_config()
    connected = m365_auth.list_connected(cfg)
    if not connected:
        return {"error": "no Microsoft 365 client is signed in yet — connect one on the M365 card"}
    if ctx.client_factory is None:
        return {"error": "no client factory available for a cross-client read"}
    ok, reason = is_allowed_read("m365", "/users")
    if not ok:
        return {"error": reason}
    base = {"$top": 999, "$select": _SELECT, "$count": "true"}
    matched, by_client, errors, truncated = [], [], [], []
    for t in connected:
        client = ctx.client_factory("m365", t)
        users, err, trunc = _scan(lambda p: client.get("/users", p), base)
        if err:
            errors.append({"tenant": t, "error": str(err.get("error"))[:160]}); continue
        hits = [_slim({**u, "tenant": t}) for u in users if _matches(u, needle)]
        matched.extend(hits)
        by_client.append({"tenant": t, "matched": len(hits), "scanned": len(users)})
        if trunc:
            truncated.append(t)
    out: dict[str, Any] = {"scope": "all_clients", "clients_searched": connected,
                           "count": len(matched), "by_client": by_client, "users": matched,
                           "searched_for": needle, "match": "contains"}
    if errors:
        out["errors"] = errors
    if truncated:
        out["note"] = ("page cap hit for: " + ", ".join(truncated) + " — matches for those "
                       "clients may be incomplete")
    return out


def _search_for(get_page: Callable[[dict], Any], name: str) -> tuple[list, Optional[dict]]:
    """One Graph keyword search for `name`. Returns (raw_users, error_or_None)."""
    data = get_page(_params(name, "", _NAME_TOP))
    if isinstance(data, dict) and data.get("error"):
        return [], data
    page = (data.get("value") if isinstance(data, dict) else data) or []
    return [u for u in page if isinstance(u, dict)], None


def _collect_names(getters: list[tuple[Optional[str], Callable[[dict], Any]]],
                   names: list[str]) -> dict:
    """Resolve each name across every getter (one per connected tenant), consolidating into a
    single result. This is what collapses N per-person tool calls into ONE (D-110)."""
    matched, by_name, not_found, errors, seen = [], [], [], [], set()
    for name in names:
        hits = 0
        for tenant, get in getters:
            users, err = _search_for(get, name)
            if err:
                row = {"name": name, "error": str(err.get("error"))[:160]}
                if tenant:
                    row["tenant"] = tenant
                errors.append(row)
                continue
            for u in users:
                hits += 1
                slim = _slim({**u, "tenant": tenant} if tenant else u)
                key = (slim.get("userPrincipalName") or "").lower() + "|" + str(tenant or "")
                if key in seen:
                    continue
                seen.add(key)
                slim["matched_query"] = name
                matched.append(slim)
        by_name.append({"name": name, "matched": hits})
        if not hits:
            not_found.append(name)
    out: dict[str, Any] = {"count": len(matched), "users": matched,
                           "by_name": by_name, "match": "names"}
    if not_found:
        out["not_found"] = not_found
    if errors:
        out["errors"] = errors
    return out


def _names_one_tenant(ctx, names: list[str]) -> dict:
    from execution.clients.scopes import scoped_read
    return _collect_names([(None, lambda p: scoped_read(ctx, "m365", "/users", p))], names)


def _names_all_clients(ctx, names: list[str]) -> dict:
    from execution.core import m365_auth
    from execution.core.config import get_config
    from execution.clients.scopes import is_allowed_read
    cfg = get_config()
    connected = m365_auth.list_connected(cfg)
    if not connected:
        return {"error": "no Microsoft 365 client is signed in yet — connect one on the M365 card"}
    if ctx.client_factory is None:
        return {"error": "no client factory available for a cross-client read"}
    ok, reason = is_allowed_read("m365", "/users")
    if not ok:
        return {"error": reason}
    getters = [(t, (lambda c: lambda p: c.get("/users", p))(ctx.client_factory("m365", t)))
               for t in connected]
    out = _collect_names(getters, names)
    out["scope"] = "all_clients"
    out["clients_searched"] = connected
    return out


def run(ctx, search: str = "", filter: str = "", name_contains: str = "",
        names: Optional[list] = None, top: int = 200, **_: Any):
    from execution.clients.scopes import scoped_read
    is_star = (getattr(ctx, "tenant_id", "") or "") == "*"
    if names:                                          # batch name→user lookup (D-110)
        wanted = [str(n).strip() for n in (names if isinstance(names, list) else [names])
                  if str(n).strip()][:_MAX_NAMES]
        if wanted:
            return _names_all_clients(ctx, wanted) if is_star else _names_one_tenant(ctx, wanted)
    needle = (name_contains or "").strip().lower()
    if needle:                                     # substring scan + client-side filter (D-93)
        return _contains_all_clients(ctx, needle) if is_star else _contains_one_tenant(ctx, needle)
    try:
        top = max(1, min(int(top), 999))
    except (TypeError, ValueError):
        top = 200
    params = _params(search, filter, top)
    q = (search or "").strip().replace('"', "")
    if is_star:                                    # cross-client view (D-51)
        return _all_clients(ctx, params, q)
    data = scoped_read(ctx, "m365", "/users", params)
    if isinstance(data, dict) and data.get("error"):
        return data
    users = [_slim(u) for u in ((data.get("value") if isinstance(data, dict) else data) or [])
             if isinstance(u, dict)]
    total = data.get("@odata.count") if isinstance(data, dict) else None
    out: dict[str, Any] = {"count": len(users), "users": users}
    if q:
        out["searched_for"] = q
    if total is not None and total > len(users):
        out["total_in_tenant"] = total
        out["note"] = (f"showing {len(users)} of {total} — narrow with `search`, or use "
                       f"`name_contains` to match a substring across the whole directory")
    elif isinstance(data, dict) and data.get("@odata.nextLink"):
        out["note"] = "more users exist beyond this page — use `search` or `name_contains` to narrow"
    return out


def _all_clients(ctx, params: dict, q: str) -> dict:
    """'All clients' (tenant '*') is a read-only cross-client view (North Star), but M365 is
    per-client — so iterate every signed-in client and aggregate, tagging each user's tenant."""
    from execution.core import m365_auth
    from execution.core.config import get_config
    from execution.clients.scopes import is_allowed_read
    cfg = get_config()
    connected = m365_auth.list_connected(cfg)
    if not connected:
        return {"error": "no Microsoft 365 client is signed in yet — connect one on the M365 card"}
    if ctx.client_factory is None:
        return {"error": "no client factory available for a cross-client read"}
    ok, reason = is_allowed_read("m365", "/users")
    if not ok:
        return {"error": reason}
    by_client, all_users, errors = [], [], []
    for t in connected:
        try:
            data = ctx.client_factory("m365", t).get("/users", params)
        except Exception as e:                     # noqa: BLE001 — one client's failure isn't fatal
            errors.append({"tenant": t, "error": str(e)[:160]}); continue
        if isinstance(data, dict) and data.get("error"):
            errors.append({"tenant": t, "error": str(data["error"])[:160]}); continue
        users = [u for u in ((data.get("value") if isinstance(data, dict) else data) or [])
                 if isinstance(u, dict)]
        all_users.extend(_slim({**u, "tenant": t}) for u in users)
        by_client.append({"tenant": t, "count": len(users)})
    out: dict[str, Any] = {"scope": "all_clients", "clients_searched": connected,
                           "count": len(all_users), "by_client": by_client, "users": all_users}
    if q:
        out["searched_for"] = q
    if errors:
        out["errors"] = errors
    out["note"] = ("aggregated across all signed-in Microsoft 365 clients — select one client in "
                   "the picker to scope to it.")
    return out
