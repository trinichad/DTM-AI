"""Shared plumbing for the UniFi Network skills (D-84). No NAME → invisible to the registry (I-1).

Most endpoints live under /v1/sites/{siteId}/…; resolve_site() turns an optional site name/id into
a siteId (defaulting to the only/Default site) so a tech never has to know the GUID.
"""
from __future__ import annotations

from typing import Any, Optional


def sites(client) -> list[dict]:
    body = client.get("/v1/sites")
    if isinstance(body, dict):
        return body.get("data") or []
    return body if isinstance(body, list) else []


def resolve_site(client, site: str = ""):
    """(site_id, None) or (None, error). Matches by id or name; else the only site, else 'Default',
    else the first."""
    needle = str(site or "").strip()
    rows = sites(client)
    if not rows:
        return None, "no sites returned by the UniFi console — check UNIFI_URL + the API key"
    if needle:
        for s in rows:
            if str(s.get("id")) == needle or str(s.get("name", "")).lower() == needle.lower():
                return s.get("id"), None
        return None, f"no UniFi site matched '{needle}'"
    if len(rows) == 1:
        return rows[0].get("id"), None
    for s in rows:
        if str(s.get("name", "")).lower() in ("default", "default site"):
            return s.get("id"), None
    return rows[0].get("id"), None


def slim(row: dict, fields: tuple) -> dict:
    picked = {k: row.get(k) for k in fields if k in row}
    return picked or row
