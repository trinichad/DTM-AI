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
            final_url = resp.geturl()
            if not raw:
                return resp.status, None
            text = raw.decode("utf-8", "replace")
            try:
                return resp.status, json.loads(text)
            except json.JSONDecodeError:
                # A 2xx that isn't JSON is almost always an HTML login/redirect or error page —
                # surface what actually came back (and where) instead of a bare parser error.
                ctype = resp.headers.get("Content-Type", "?")
                note = f"; redirected to {final_url}" if final_url != url else ""
                snippet = " ".join(text.split())[:200]
                raise HttpError(resp.status, f"expected JSON, got {ctype}{note}: {snippet}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        raise HttpError(e.code, raw) from e


def http_stream(
    method: str,
    url: str,
    headers: Optional[dict[str, str]] = None,
    params: Optional[dict[str, Any]] = None,
    json_body: Optional[Any] = None,
    *,
    timeout: float = 120.0,
    verify_tls: bool = True,
):
    """Stream a response line-by-line (yields decoded text lines, blanks dropped).

    For incremental LLM responses: Ollama emits newline-delimited JSON, Anthropic/OpenAI emit
    SSE (`data: {...}` lines). The caller parses each line per its provider. Like http_json, the
    transport is injectable so providers can be unit-tested with a canned line iterator.
    """
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    data = None
    hdrs = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    hdrs.setdefault("Accept", "text/event-stream, application/x-ndjson, application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
    ctx = ssl.create_default_context()
    if not verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310
            for raw in resp:                       # urllib responses iterate by line
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line:
                    yield line
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        raise HttpError(e.code, raw) from e


class DownloadTooLarge(Exception):
    """A streamed download exceeded its byte cap (the partial file is removed)."""


def http_bytes(method: str, url: str, headers: Optional[dict[str, str]] = None, *,
               timeout: float = 120.0, verify_tls: bool = True) -> tuple[int, bytes]:
    """GET/HEAD that returns RAW bytes (not JSON-decoded) — for Azure Blob list XML, etc.
    Same injectable-shape contract as http_json so tests can stub it."""
    req = urllib.request.Request(url, headers=dict(headers or {}), method=method.upper())
    ctx = ssl.create_default_context()
    if not verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        raise HttpError(e.code, raw) from e


def download_to_file(method: str, url: str, *, dest_path: str,
                     headers: Optional[dict[str, str]] = None, max_bytes: Optional[int] = None,
                     timeout: float = 600.0, chunk: int = 1 << 20, on_progress=None,
                     verify_tls: bool = True) -> int:
    """Stream a response body to `dest_path` in `chunk`-sized pieces, returning bytes written.
    Enforces `max_bytes` AS IT DOWNLOADS (so an oversized blob can't fill the disk first) and
    removes the partial file if the cap is hit or the transfer errors. `on_progress(bytes_so_far)`
    is called per chunk."""
    import os
    req = urllib.request.Request(url, headers=dict(headers or {}), method=method.upper())
    ctx = ssl.create_default_context()
    if not verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    total = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310
            with open(dest_path, "wb") as f:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    total += len(buf)
                    if max_bytes is not None and total > max_bytes:
                        raise DownloadTooLarge(f"exceeded cap of {max_bytes} bytes")
                    f.write(buf)
                    if on_progress:
                        on_progress(total)
    except urllib.error.HTTPError as e:
        _silent_remove(dest_path)
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        raise HttpError(e.code, raw) from e
    except Exception:
        _silent_remove(dest_path)
        raise
    return total


def _silent_remove(path: str) -> None:
    import os
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


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
