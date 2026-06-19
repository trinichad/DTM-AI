"""Freshdesk API v2 client (D-83). Basic auth (api_key:X), urllib-based.

Bounded writes mirror the Kaseya/Cylance pattern: only allow-listed (method, path) shapes may
mutate; whether a write may run at all is decided upstream in dispatch() (CATEGORY=write + the
Capability Console + approval). Rate-limited per-account (Freshdesk: 100-700/min by plan) with a
conservative sliding window + 429 backoff, shared across the chat loop and the scheduler.
"""
from __future__ import annotations

import base64
import re
import threading
import time
from collections import deque
from typing import Any, Callable, Iterator, Optional

from ._http import HttpError, http_json


class _RateLimiter:
    def __init__(self, capacity: int, window_seconds: float) -> None:
        self.capacity = capacity
        self.window = window_seconds
        self._ts: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            while self._ts and now - self._ts[0] > self.window:
                self._ts.popleft()
            if len(self._ts) >= self.capacity:
                wait = self.window - (now - self._ts[0]) + 0.05
                if wait > 0:
                    time.sleep(wait)
                now = time.monotonic()
                while self._ts and now - self._ts[0] > self.window:
                    self._ts.popleft()
            self._ts.append(now)


class FreshdeskClient:
    _LIMITER = _RateLimiter(capacity=80, window_seconds=60.0)   # safe under the 100/min Free floor

    # bounded write surface (D-83) — allow-list only; dispatch() decides whether a write may run.
    WRITE_RULES: tuple[tuple[str, str], ...] = (
        ("POST", r"^/tickets$"),
        ("PUT", r"^/tickets/\d+$"),
        ("POST", r"^/tickets/\d+/reply$"),
        ("POST", r"^/tickets/\d+/notes$"),
        ("POST", r"^/tickets/\d+/forward$"),
        ("PUT", r"^/tickets/\d+/restore$"),
        ("POST", r"^/tickets/merge$"),
        ("POST", r"^/tickets/\d+/time_entries$"),
        ("PUT", r"^/tickets/\d+/time_entries/\d+$"),
        ("POST", r"^/contacts$"),
        ("PUT", r"^/contacts/\d+$"),
        ("PUT", r"^/contacts/\d+/make_agent$"),
        ("POST", r"^/companies$"),
        ("PUT", r"^/companies/\d+$"),
        ("POST", r"^/groups$"),
        ("PUT", r"^/groups/\d+$"),
        ("POST", r"^/solutions/folders/\d+/articles$"),
        ("PUT", r"^/solutions/articles/\d+$"),
    )
    DESTRUCTIVE_RULES: tuple[tuple[str, str], ...] = (
        ("DELETE", r"^/tickets/\d+$"),
        ("DELETE", r"^/contacts/\d+$"),
        ("DELETE", r"^/companies/\d+$"),
        ("DELETE", r"^/groups/\d+$"),
        ("DELETE", r"^/tickets/\d+/time_entries/\d+$"),
    )

    def __init__(self, domain: str, api_key: str, *, verify_tls: bool = True,
                 transport: Callable = http_json) -> None:
        if not (domain and api_key):
            raise ValueError("domain and api_key are required")
        host = re.sub(r"^https?://", "", str(domain).strip()).split("/")[0].rstrip("/")
        if not host.endswith(".freshdesk.com"):
            host = f"{host}.freshdesk.com"
        self.base = f"https://{host}/api/v2"
        creds = base64.b64encode(f"{api_key}:X".encode()).decode()   # password is ignored by Freshdesk
        self._headers = {"Authorization": f"Basic {creds}"}
        self.verify = verify_tls
        self._t = transport

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        self._LIMITER.acquire()
        try:
            _s, data = self._t("GET", f"{self.base}{path}", headers=self._headers, params=params)
            return data
        except HttpError as e:
            if e.status == 429:
                time.sleep(5)
                self._LIMITER.acquire()
                _s, data = self._t("GET", f"{self.base}{path}", headers=self._headers, params=params)
                return data
            raise

    def get_paginated(self, path: str, params: Optional[dict] = None, *, per_page: int = 100,
                      max_pages: int = 100) -> Iterator[dict]:
        """Freshdesk list endpoints return a bare JSON array; search endpoints return
        {results:[…], total:N}. Yield rows from either, stopping on a short page."""
        params = dict(params or {})
        params["per_page"] = per_page
        for page in range(1, max_pages + 1):
            params["page"] = page
            body = self.get(path, params)
            if isinstance(body, list):
                items = body
            elif isinstance(body, dict):
                items = next((body[k] for k in ("results", "articles", "time_entries")
                              if isinstance(body.get(k), list)), None)
                if items is None:
                    items = next((v for v in body.values() if isinstance(v, list)), [])
            else:
                return
            yield from items
            if len(items) < per_page:
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
        self._LIMITER.acquire()
        try:
            _s, data = self._t(method, f"{self.base}{path}", headers=self._headers, json_body=body)
            return data if data is not None else {"ok": True}
        except HttpError as e:
            return {"error": f"{e.status}: {str(getattr(e, 'body', '') or e)[:400]}"}

    def probe(self) -> dict[str, Any]:
        payload = self.get("/settings/helpdesk") or {}
        lang = payload.get("primary_language") if isinstance(payload, dict) else None
        return {"ok": True, "detail": f"auth ok; freshdesk {self.base.split('//')[1].split('.')[0]}"
                                      + (f" ({lang})" if lang else "")}
