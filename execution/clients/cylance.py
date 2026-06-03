"""Cylance Console API client — read-only. Ported from Kaseya Link, urllib-based.

Auth: a JWT (HS256, signed with CYLANCE_APP_SECRET via the tested encode_jwt_hs256)
exchanged at POST {base}/auth/v2/token for a bearer (cached ~25 min). Region selects base.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Iterator, Optional

from ._http import HttpError, encode_jwt_hs256, http_json

REGION_BASE_URLS: dict[str, str] = {
    "NA": "https://protectapi.cylance.com",
    "EU": "https://protectapi-euc1.cylance.com",
    "APN": "https://protectapi-apne1.cylance.com",
    "APS": "https://protectapi-au.cylance.com",
    "AU": "https://protectapi-au.cylance.com",
    "SAE": "https://protectapi-sae1.cylance.com",
    "GOV": "https://protectapi-us.cylance.com",
}
_TOKEN_TTL = 25 * 60


class CylanceClient:
    def __init__(
        self, region: str, tenant_id: str, app_id: str, app_secret: str,
        *, verify_tls: bool = True, transport: Callable = http_json,
    ) -> None:
        key = (region or "NA").split("#")[0].strip().upper()
        if key not in REGION_BASE_URLS:
            raise ValueError(f"unknown CYLANCE_REGION '{region}'. Allowed: {sorted(REGION_BASE_URLS)}")
        self.base = REGION_BASE_URLS[key]
        self.tenant_id = tenant_id
        self.app_id = app_id
        self.app_secret = app_secret
        self.verify = verify_tls
        self._t = transport
        self._token: Optional[str] = None
        self._expires = 0.0

    def _ensure_token(self) -> None:
        if self._token and time.time() < self._expires:
            return
        now = int(time.time())
        claims = {"exp": now + 1800, "iat": now, "iss": "http://cylance.com",
                  "sub": self.app_id, "tid": self.tenant_id, "jti": str(uuid.uuid4())}
        jwt = encode_jwt_hs256(claims, self.app_secret)
        _status, data = self._t("POST", f"{self.base}/auth/v2/token",
                               json_body={"auth_token": jwt})
        token = (data or {}).get("access_token")
        if not token:
            raise RuntimeError(f"Cylance auth response missing access_token: {data}")
        self._token = token
        self._expires = time.time() + _TOKEN_TTL

    def _headers(self) -> dict[str, str]:
        self._ensure_token()
        return {"Authorization": f"Bearer {self._token}"}

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        try:
            _s, data = self._t("GET", f"{self.base}{path}", headers=self._headers(), params=params)
            return data
        except HttpError as e:
            if e.status == 401:  # stale token -> refresh once
                self._token = None
                _s, data = self._t("GET", f"{self.base}{path}", headers=self._headers(), params=params)
                return data
            raise

    def get_paginated(self, path: str, params: Optional[dict] = None,
                      *, page_size: int = 200, max_pages: int = 200) -> Iterator[dict]:
        # Terminate on the API's own total_pages (like the proven Kaseya Link client). Relying only
        # on "a short page" over-reads to max_pages when the API keeps returning full pages (which
        # produced the bogus 10,000 = 50 pages x 200 count). max_pages stays as a hard safety stop.
        params = dict(params or {})
        params.setdefault("page_size", page_size)
        for page_number in range(1, max_pages + 1):
            # Cylance's REQUEST param is `page`; its RESPONSE echoes `page_number`. The old client
            # sent only `page_number`, which Cylance ignored -> it returned page 1 every time (every
            # page identical -> bogus 1800 raw / 200 unique). We send `page` (the real one) and keep
            # `page_number` too; unknown query params are ignored, so this is correct either way.
            params["page"] = page_number
            params["page_number"] = page_number
            payload = self.get(path, params) or {}
            items = payload.get("page_items") or []
            yield from items
            total_pages = int(payload.get("total_pages") or 0)
            if not items:
                return
            if total_pages and page_number >= total_pages:
                return
            if not total_pages and len(items) < page_size:
                return

    def probe(self) -> dict[str, Any]:
        self._ensure_token()
        return {"ok": True, "detail": "auth ok; token issued"}
