"""Tiny stdlib HTTP/JSON helper + JWT — zero third-party deps (no requests/httpx/PyJWT).

Keeps the clients runnable and testable anywhere. Clients accept an injectable
`transport` (default = http_json) so tests can stub responses with no network.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


class HttpError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


# transport signature: (method, url, headers, params, json_body) -> (status:int, data:Any)
def http_json(
    method: str,
    url: str,
    headers: Optional[dict[str, str]] = None,
    params: Optional[dict[str, Any]] = None,
    json_body: Optional[Any] = None,
    *,
    timeout: float = 60.0,
    verify_tls: bool = True,
) -> tuple[int, Any]:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    data = None
    hdrs = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    hdrs.setdefault("Accept", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
    ctx = ssl.create_default_context()
    if not verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310
            raw = resp.read()
            body = json.loads(raw.decode("utf-8")) if raw else None
            return resp.status, body
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        raise HttpError(e.code, raw) from e


# ── JWT HS256 (replaces the hand-rolled signer; small + tested) ─────────────
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def encode_jwt_hs256(claims: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    head = _b64url(json.dumps(header, separators=(",", ":")).encode())
    body = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{head}.{body}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{head}.{body}.{_b64url(sig)}"
