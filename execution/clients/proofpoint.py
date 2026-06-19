"""Proofpoint Essentials API v1 client (D-86).

Auth: the Essentials `X-User` / `X-Password` headers. Base is region-derived
(https://<region>.proofpointessentials.com/api/v1) or a full URL. Bounded writes mirror the
Kaseya/Cylance pattern; dispatch() still gates whether a write may run (CATEGORY=write + Capability
Console + approval). Orgs are addressed by their primary domain (/orgs/<domain>).
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from ._http import HttpError, http_json


class ProofpointClient:
    WRITE_RULES: tuple[tuple[str, str], ...] = (
        ("POST", r"^/orgs/[^/?]+/users$"),          # create user
        ("PUT", r"^/orgs/[^/?]+/users/[^/?]+$"),    # update user (incl. safe/blocked sender lists)
        ("PUT", r"^/orgs/[^/?]+$"),                 # update org settings
    )
    DESTRUCTIVE_RULES: tuple[tuple[str, str], ...] = (
        ("DELETE", r"^/orgs/[^/?]+/users/[^/?]+$"),  # delete user
    )

    def __init__(self, region_or_url: str, user: str, password: str, *, verify_tls: bool = True,
                 transport: Callable = http_json) -> None:
        if not (region_or_url and user and password):
            raise ValueError("region (or base URL), user, and password are required")
        spec = str(region_or_url).strip().rstrip("/")
        if spec.startswith("http"):
            base = spec if "/api/v1" in spec else spec + "/api/v1"
        else:
            base = f"https://{spec}.proofpointessentials.com/api/v1"
        self.base = base
        self._headers = {"X-User": user, "X-Password": password}
        self.verify = verify_tls
        self._t = transport

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        _s, data = self._t("GET", f"{self.base}{path}", headers=self._headers, params=params,
                           verify_tls=self.verify)
        return data

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
        # /endpoints/<domain> needs a domain; the lightest auth check is the API root.
        self.get("/")
        return {"ok": True, "detail": f"auth ok; {self.base.split('//')[1].split('.')[0]}"}
