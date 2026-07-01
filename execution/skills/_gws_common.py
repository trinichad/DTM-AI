"""Shared helpers for Google Workspace read skills (D-118).

Keeps each gws_* read skill small: Directory-API pagination (nextPageToken), the one-tenant vs
'All clients' (*) split, and the connected-clients guard — mirroring the M365 read pattern but
Google-shaped. Everything routes through scopes.scoped_read so the allowlist is always enforced.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

_MAX_PAGES = 40                      # 40 × 500 = 20k rows — far beyond any MSP client; bounds a scan
PAGE = 500                           # Directory API maxResults cap


def scan(get_page: Callable[[dict], Any], base: dict, items_key: str,
         max_pages: int = _MAX_PAGES) -> tuple[list, Optional[dict], bool]:
    """Follow Directory nextPageToken, collecting `items_key` rows. Returns (items, err, truncated)."""
    items: list = []
    params = dict(base)
    for _ in range(max_pages):
        data = get_page(params)
        if isinstance(data, dict) and data.get("error"):
            return items, data, False
        page = (data.get(items_key) if isinstance(data, dict) else data) or []
        items.extend(x for x in page if isinstance(x, dict))
        nxt = data.get("nextPageToken") if isinstance(data, dict) else None
        if not nxt:
            return items, None, False
        params = {**base, "pageToken": nxt}
    return items, None, True


def connected(ctx, path: str) -> tuple[Any, Optional[list]]:
    """(cfg, connected_clients) for an all-clients read, or ({'error':...}, None) — fail-closed if
    no client is signed in, the path isn't allowlisted, or there's no client factory."""
    from execution.core import gws_auth
    from execution.core.config import get_config
    from execution.clients.scopes import is_allowed_read
    cfg = get_config()
    clients = gws_auth.list_connected(cfg)
    if not clients:
        return {"error": "no Google Workspace client is signed in yet — connect one on the "
                         "Google Workspace card"}, None
    if ctx.client_factory is None:
        return {"error": "no client factory available for a cross-client read"}, None
    ok, reason = is_allowed_read("gws", path)
    if not ok:
        return {"error": reason}, None
    return cfg, clients


def read_list(ctx, path: str, base: dict, items_key: str, slim: Callable[[dict], dict],
              *, out_key: str = "items") -> dict:
    """Standard one-tenant vs all-clients Directory list. `slim` compacts each row; in all-clients
    mode each row is tagged with its `tenant`."""
    is_star = (getattr(ctx, "tenant_id", "") or "") == "*"
    if not is_star:
        from execution.clients.scopes import scoped_read
        items, err, trunc = scan(lambda p: scoped_read(ctx, "gws", path, p), base, items_key)
        if err:
            return err
        out: dict[str, Any] = {"count": len(items), out_key: [slim(x) for x in items]}
        if trunc:
            out["note"] = "hit the page cap — more may exist; narrow the query"
        return out
    cfg, clients = connected(ctx, path)
    if clients is None:
        return cfg
    rows, by_client, errors, truncated = [], [], [], []
    for t in clients:
        client = ctx.client_factory("gws", t)
        items, err, trunc = scan(lambda p: client.get(path, p), base, items_key)
        if err:
            errors.append({"tenant": t, "error": str(err.get("error"))[:160]}); continue
        rows.extend(slim({**x, "tenant": t}) for x in items)
        by_client.append({"tenant": t, "count": len(items)})
        if trunc:
            truncated.append(t)
    out = {"scope": "all_clients", "clients_searched": clients, "count": len(rows),
           "by_client": by_client, out_key: rows}
    if errors:
        out["errors"] = errors
    if truncated:
        out["note"] = "page cap hit for: " + ", ".join(truncated)
    return out
