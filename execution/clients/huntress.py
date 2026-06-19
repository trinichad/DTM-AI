"""Huntress Public API v1 client — read-only. Ported from Kaseya Link, urllib-based.

Auth: HTTP Basic key:secret. Includes the thread-safe sliding-window rate limiter
(50/60s headroom under the 60/min cap) + 429 Retry-After backoff, shared across the
chat loop and the scheduler.
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


class HuntressClient:
    BASE_URL = "https://api.huntress.io/v1"
    _LIMITER = _RateLimiter(capacity=50, window_seconds=60.0)

    def __init__(self, api_key: str, api_secret: str, *, verify_tls: bool = True,
                 transport: Callable = http_json) -> None:
        if not (api_key and api_secret):
            raise ValueError("api_key and api_secret are required")
        creds = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        self._headers = {"Authorization": f"Basic {creds}"}
        self.verify = verify_tls
        self._t = transport

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        self._LIMITER.acquire()
        try:
            _s, data = self._t("GET", f"{self.BASE_URL}{path}", headers=self._headers, params=params)
            return data
        except HttpError as e:
            if e.status == 429:
                time.sleep(5)
                self._LIMITER.acquire()
                _s, data = self._t("GET", f"{self.BASE_URL}{path}", headers=self._headers, params=params)
                return data
            raise

    def get_paginated(self, path: str, params: Optional[dict] = None, *, limit: int = 100,
                      max_pages: int = 100) -> Iterator[dict]:
        keys = ("agents", "organizations", "incident_reports", "summary_reports",
                "billing_reports", "escalations", "items", "data", "results")
        params = dict(params or {})
        params["limit"] = limit
        for page in range(1, max_pages + 1):
            params["page"] = page
            body = self.get(path, params) or {}
            if isinstance(body, list):
                items = body
            else:
                items = next((body[k] for k in keys if isinstance(body.get(k), list)), None)
                if items is None:
                    items = next((v for v in body.values() if isinstance(v, list)), [])
            yield from items
            if len(items) < limit:
                return

    # ── bounded writes (D-82) — Huntress's first write APIs (escalation/incident responses). ──
    # Allowlist only; dispatch() gates whether a write may run (CATEGORY=write + approval).
    WRITE_RULES: tuple[tuple[str, str], ...] = (
        ("POST", r"^/escalations/\d+/resolution$"),                 # resolve an escalation
        ("POST", r"^/incident_reports/\d+/resolution$"),            # resolve an incident report
        ("POST", r"^/accounts/\d+/incident_reports/\d+/remediations/bulk_approval$"),
        ("POST", r"^/accounts/\d+/incident_reports/\d+/remediations/bulk_rejection$"),
    )

    @staticmethod
    def _match(rules: tuple, method: str, path: str) -> bool:
        base = path.split("?", 1)[0]
        return any(m == method and re.match(pat, base) for m, pat in rules)

    def write(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        if not self._match(self.WRITE_RULES, method, path):
            return {"error": f"write not allowed: {method} {path}"}
        self._LIMITER.acquire()
        try:
            _s, data = self._t(method, f"{self.BASE_URL}{path}", headers=self._headers,
                               json_body=body)
            return data if data is not None else {"ok": True}
        except HttpError as e:
            return {"error": f"{e.status}: {str(getattr(e, 'body', '') or e)[:300]}"}

    def probe(self) -> dict[str, Any]:
        payload = self.get("/account") or {}
        label = (payload.get("name") or payload.get("account_name")
                 or payload.get("domain") or payload.get("id") or "unknown")
        return {"ok": True, "detail": f"auth ok; account={label}"}
