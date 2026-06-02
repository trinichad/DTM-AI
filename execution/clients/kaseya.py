"""Kaseya VSA X REST client (API v2) — read-only. urllib-based, injectable transport.

Auth: HTTP Basic with a token-id / token-secret pair. Every request carries
`Authorization: Basic base64(KASEYA_TOKEN_ID:KASEYA_TOKEN_SECRET)` — there is NO /auth
token-exchange round-trip (that was the older VSA 9.5 scheme). Base URL is KASEYA_URL
with the `/vsa/api/v2` prefix appended. Matches the live DTM tenant (ks2.dtmconsulting.com).

Read-only: only GET is issued. v2 response envelopes vary by endpoint, so `_as_list`
normalizes the common shapes (bare list, or a dict wrapping the rows) to a list of dicts.
"""
from __future__ import annotations

import base64
from typing import Any, Callable, Optional

from ._http import http_json

API_PREFIX = "/vsa/api/v2"


class KaseyaClient:
    def __init__(
        self, base_url: str, token_id: Optional[str] = None, token_secret: Optional[str] = None,
        *, verify_tls: bool = True, transport: Callable = http_json,
    ) -> None:
        self.base = base_url.rstrip("/") + API_PREFIX
        self.token_id = token_id
        self.token_secret = token_secret
        self.verify = verify_tls
        self._t = transport

    # ── auth ──
    def _auth_header(self) -> dict[str, str]:
        if not (self.token_id and self.token_secret):
            raise RuntimeError("KASEYA_TOKEN_ID/KASEYA_TOKEN_SECRET required")
        creds = base64.b64encode(f"{self.token_id}:{self.token_secret}".encode()).decode()
        return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

    # ── requests ──
    def get(self, path: str, params: Optional[dict] = None) -> Any:
        _status, data = self._t("GET", f"{self.base}{path}",
                                headers=self._auth_header(), params=params)
        return data

    @staticmethod
    def _as_list(data: Any) -> list[dict]:
        """Normalize a v2 response to a list of row dicts (shape varies by endpoint)."""
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict):
            for key in ("Result", "result", "Data", "data", "items", "Items", "assets"):
                v = data.get(key)
                if isinstance(v, list):
                    return [r for r in v if isinstance(r, dict)]
            return [data]
        return []

    # ── read convenience ──
    def get_assets(self, filters: Optional[str] = None) -> list[dict]:
        params = {"filter": filters} if filters else None
        return self._as_list(self.get("/assetmgmt/asset", params))

    def get_asset(self, asset_id: str) -> dict:
        needle = str(asset_id)
        keys = ("AgentId", "AgentGuid", "AssetId", "id", "Id")
        for a in self.get_assets():
            if needle in {str(a.get(k)) for k in keys if a.get(k) is not None}:
                return a
        return {}

    def probe(self) -> dict[str, Any]:
        assets = self.get_assets()
        return {"ok": True, "detail": f"auth ok; {len(assets)} assets visible"}
