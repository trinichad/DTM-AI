"""Kaseya VSA 9.5 REST client — read-only. Faithful stdlib port of the proven Kaseya Link client.

Auth (verified working against ks2.dtmconsulting.com): plain Basic `base64(user:pass)` →
`GET /api/v1.0/auth` → `Result.Token`, then `Authorization: Bearer <token>` for ~20 min.
A pre-issued KASEYA_TOKEN (Bearer) is used directly if present, skipping /auth.

NOTE: the `msp-ai-ops` build's token-id/token-secret-against-/vsa/api/v2 scheme never actually
authenticated (returned the VSA web "Whoops." page / ResponseCode 4010001). This client uses the
USER/PASS → token-exchange flow that Kaseya Link proved out on the live tenant.
"""
from __future__ import annotations

import base64
import time
from typing import Any, Callable, Optional

from ._http import http_json


class KaseyaClient:
    def __init__(
        self, base_url: str, username: Optional[str] = None, password: Optional[str] = None,
        *, token: Optional[str] = None, verify_tls: bool = True,
        transport: Callable = http_json,
    ) -> None:
        self.base = base_url.rstrip("/") + "/api/v1.0"
        self.username = username
        self.password = password
        self.verify = verify_tls
        self._t = transport
        self.token = token
        self.static_token = bool(token)
        self.token_expires = float("inf") if token else 0.0

    # ── auth ──
    def login(self) -> None:
        if self.static_token:
            return
        if not (self.username and self.password):
            raise RuntimeError("KASEYA_USER/KASEYA_PASS required when no KASEYA_TOKEN is set")
        creds = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        _status, data = self._t("GET", f"{self.base}/auth",
                                headers={"Authorization": f"Basic {creds}"})
        result = (data or {}).get("Result") or {}
        self.token = result.get("Token")
        self.token_expires = time.time() + 20 * 60
        if not self.token:
            raise RuntimeError(f"Kaseya login failed: {data}")

    def _auth_header(self) -> dict[str, str]:
        if not self.token or time.time() > self.token_expires:
            self.login()
        return {"Authorization": f"Bearer {self.token}"}

    # ── requests ──
    def get(self, path: str, params: Optional[dict] = None) -> Any:
        _status, data = self._t("GET", f"{self.base}{path}",
                                headers=self._auth_header(), params=params)
        return data

    def get_all(self, path: str, params: Optional[dict] = None, page_size: int = 100) -> list[dict]:
        params = dict(params or {})
        params["$top"] = page_size
        out: list[dict] = []
        skip = 0
        while True:
            params["$skip"] = skip
            data = self.get(path, params) or {}
            page = data.get("Result") or []
            out.extend(page)
            total = data.get("TotalRecords")
            if total is not None and len(out) >= int(total):
                break
            if len(page) < page_size:
                break
            skip += len(page)
        return out

    # ── read convenience ──
    def get_assets(self, filters: Optional[str] = None) -> list[dict]:
        return self.get_all("/assetmgmt/assets", {"$filter": filters} if filters else {})

    def get_asset(self, asset_id: str) -> dict:
        needle = str(asset_id)
        for a in self.get_all("/assetmgmt/assets"):
            if needle in (str(a.get("AgentId")), str(a.get("AgentGuid")), str(a.get("AssetId"))):
                return a
        return {}

    def get_orgs(self) -> list[dict]:
        return (self.get("/system/orgs") or {}).get("Result", [])

    def probe(self) -> dict[str, Any]:
        # Validate against assets (read-only bridge accounts can read these but are often
        # denied /system/orgs — a 403 there is a scope limit, not an auth failure). Mirrors
        # the proven Kaseya Link probe.
        data = self.get("/assetmgmt/assets", {"$top": 1}) or {}
        rows = data.get("Result") or []
        return {"ok": True, "detail": f"auth ok; /assetmgmt/assets returned {len(rows)} row(s)"}
