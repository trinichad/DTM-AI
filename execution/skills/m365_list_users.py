"""List / SEARCH Microsoft 365 / Entra users via Microsoft Graph (D-32) — read-only."""
from __future__ import annotations

from typing import Any

NAME = "m365_list_users"
DESCRIPTION = ("List or search Microsoft 365 / Entra users (display name, sign-in name, email, "
               "enabled state, job title, department). Use `search` to find specific people by "
               "name/email (best for big tenants), or `filter` for an OData query. Scoped to the "
               "selected client; when the session is 'All clients' (*) it aggregates across every "
               "signed-in M365 client and tags each user with their `tenant`.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
_SELECT = "id,displayName,userPrincipalName,mail,accountEnabled,jobTitle,department"
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "search": {"type": "string",
                   "description": "find users whose name/email/sign-in contains this text "
                                  "(e.g. 'smith') — the right tool for large tenants"},
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
        users = (data.get("value") if isinstance(data, dict) else data) or []
        for u in users:
            if isinstance(u, dict):
                u["tenant"] = t
        all_users.extend(users)
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


def run(ctx, search: str = "", filter: str = "", top: int = 200, **_: Any):
    from execution.clients.scopes import scoped_read
    try:
        top = max(1, min(int(top), 999))
    except (TypeError, ValueError):
        top = 200
    params = _params(search, filter, top)
    q = (search or "").strip().replace('"', "")
    if (getattr(ctx, "tenant_id", "") or "") == "*":   # cross-client view (D-51)
        return _all_clients(ctx, params, q)
    data = scoped_read(ctx, "m365", "/users", params)
    if isinstance(data, dict) and data.get("error"):
        return data
    users = (data.get("value") if isinstance(data, dict) else data) or []
    total = data.get("@odata.count") if isinstance(data, dict) else None
    out: dict[str, Any] = {"count": len(users), "users": users}
    if q:
        out["searched_for"] = q
    if total is not None and total > len(users):
        out["total_in_tenant"] = total
        out["note"] = f"showing {len(users)} of {total} — narrow with `search` to find specific people"
    elif isinstance(data, dict) and data.get("@odata.nextLink"):
        out["note"] = "more users exist beyond this page — use `search` to narrow"
    return out
