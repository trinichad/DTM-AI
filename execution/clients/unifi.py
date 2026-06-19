"""UniFi Network local Integration API client (D-84) — self-hosted UniFi OS server.

Base: <console>/proxy/network/integration · auth: per-console API key in the X-API-Key header ·
self-signed TLS supported (verify_tls defaults False for a LAN console). Bounded writes mirror the
Kaseya/Cylance pattern; dispatch() still gates whether a write may run (CATEGORY=write + Capability
Console + approval).
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterator, Optional

from ._http import HttpError, http_json

_INTEGRATION_PATH = "/proxy/network/integration"


class UnifiClient:
    WRITE_RULES: tuple[tuple[str, str], ...] = (
        ("POST", r"^/v1/sites/[^/?]+/devices$"),                                  # adopt device
        ("POST", r"^/v1/sites/[^/?]+/devices/[^/?]+/actions$"),                   # device action
        ("POST", r"^/v1/sites/[^/?]+/devices/[^/?]+/interfaces/ports/\d+/actions$"),  # port action
        ("POST", r"^/v1/sites/[^/?]+/clients/[^/?]+/actions$"),                   # client action
        ("POST", r"^/v1/sites/[^/?]+/hotspot/vouchers$"),                         # create voucher
        ("POST", r"^/v1/sites/[^/?]+/networks$"),
        ("PUT", r"^/v1/sites/[^/?]+/networks/[^/?]+$"),
        ("POST", r"^/v1/sites/[^/?]+/wifi/broadcasts$"),
        ("PUT", r"^/v1/sites/[^/?]+/wifi/broadcasts/[^/?]+$"),
        ("POST", r"^/v1/sites/[^/?]+/firewall/zones$"),
        ("PUT", r"^/v1/sites/[^/?]+/firewall/zones/[^/?]+$"),
        ("POST", r"^/v1/sites/[^/?]+/firewall/policies$"),
        ("PUT", r"^/v1/sites/[^/?]+/firewall/policies/[^/?]+$"),
        ("PATCH", r"^/v1/sites/[^/?]+/firewall/policies/[^/?]+$"),
        ("PUT", r"^/v1/sites/[^/?]+/firewall/policies/ordering$"),
        ("POST", r"^/v1/sites/[^/?]+/dns/policies$"),
        ("PUT", r"^/v1/sites/[^/?]+/dns/policies/[^/?]+$"),
        ("POST", r"^/v1/sites/[^/?]+/acl-rules$"),
        ("PUT", r"^/v1/sites/[^/?]+/acl-rules/[^/?]+$"),
        ("PUT", r"^/v1/sites/[^/?]+/acl-rules/ordering$"),
        ("POST", r"^/v1/sites/[^/?]+/traffic-matching-lists$"),
        ("PUT", r"^/v1/sites/[^/?]+/traffic-matching-lists/[^/?]+$"),
    )
    DESTRUCTIVE_RULES: tuple[tuple[str, str], ...] = (
        ("DELETE", r"^/v1/sites/[^/?]+/devices/[^/?]+$"),                  # forget device
        ("DELETE", r"^/v1/sites/[^/?]+/hotspot/vouchers(/[^/?]+)?$"),      # delete voucher(s)
        ("DELETE", r"^/v1/sites/[^/?]+/networks/[^/?]+$"),
        ("DELETE", r"^/v1/sites/[^/?]+/wifi/broadcasts/[^/?]+$"),
        ("DELETE", r"^/v1/sites/[^/?]+/firewall/(zones|policies)/[^/?]+$"),
        ("DELETE", r"^/v1/sites/[^/?]+/dns/policies/[^/?]+$"),
        ("DELETE", r"^/v1/sites/[^/?]+/acl-rules/[^/?]+$"),
        ("DELETE", r"^/v1/sites/[^/?]+/traffic-matching-lists/[^/?]+$"),
    )

    def __init__(self, base_url: str, api_key: str, *, verify_tls: bool = False,
                 transport: Callable = http_json) -> None:
        if not (base_url and api_key):
            raise ValueError("base_url and api_key are required")
        host = str(base_url).strip().rstrip("/")
        if not host.startswith("http"):
            host = "https://" + host
        if _INTEGRATION_PATH not in host:
            host = host + _INTEGRATION_PATH
        self.base = host
        self._headers = {"X-API-Key": api_key}
        self.verify = bool(verify_tls)
        self._t = transport

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        _s, data = self._t("GET", f"{self.base}{path}", headers=self._headers, params=params,
                           verify_tls=self.verify)
        return data

    def get_paginated(self, path: str, params: Optional[dict] = None, *, limit: int = 200,
                      max_pages: int = 50) -> Iterator[dict]:
        """UniFi list endpoints wrap rows in {offset,limit,count,totalCount,data:[…]}; a non-list
        object (e.g. /v1/info) is yielded once."""
        params = dict(params or {})
        params["limit"] = limit
        offset = 0
        for _ in range(max_pages):
            params["offset"] = offset
            body = self.get(path, params)
            if isinstance(body, dict):
                items = body.get("data") if isinstance(body.get("data"), list) else None
                if items is None:
                    yield body
                    return
                total = body.get("totalCount")
            elif isinstance(body, list):
                items, total = body, None
            else:
                return
            yield from items
            offset += len(items)
            if not items or len(items) < limit or (total is not None and offset >= int(total)):
                return

    @staticmethod
    def _match(rules: tuple, method: str, path: str) -> bool:
        base = path.split("?", 1)[0]
        return any(m == method and re.match(pat, base) for m, pat in rules)

    def write(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        if not self._match(self.WRITE_RULES, method, path):
            return {"error": f"write not allowed: {method} {path}"}
        return self._write(method, path, body)

    def write_destructive(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        if not self._match(self.DESTRUCTIVE_RULES, method, path):
            return {"error": f"destructive write not allowed: {method} {path}"}
        return self._write(method, path, body)

    def _write(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        try:
            _s, data = self._t(method, f"{self.base}{path}", headers=self._headers,
                               json_body=body, verify_tls=self.verify)
            return data if data is not None else {"ok": True}
        except HttpError as e:
            return {"error": f"{e.status}: {str(getattr(e, 'body', '') or e)[:400]}"}

    def probe(self) -> dict[str, Any]:
        body = self.get("/v1/sites") or {}
        rows = body.get("data") if isinstance(body, dict) else body
        n = len(rows) if isinstance(rows, list) else "?"
        return {"ok": True, "detail": f"auth ok; {n} site(s) on the UniFi console"}
