"""Generic HTTP client for owner-defined custom integrations (D-27) — read-only.

Built ONLY via credentials.require() like every other client (I-2). Auth is injected
server-side from the integration's metadata record; secret values never reach the model,
a tool result, or the browser. GET-only — writes to a custom integration would be a new,
deliberate elevation (separate primitive + Capability Console), not a tweak here.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ._http import HttpError, http_json


class CustomHTTPClient:
    def __init__(self, integration_id: str, base_url: str, auth: dict, env: dict[str, str],
                 *, probe_path: str = "", read_paths: Optional[list[str]] = None,
                 verify_tls: bool = True, transport: Callable = http_json) -> None:
        if not base_url:
            raise ValueError(f"custom integration '{integration_id}' has no base_url configured")
        self.integration_id = integration_id
        self.base_url = base_url.rstrip("/")
        self.probe_path = probe_path
        self.read_paths = list(read_paths or [])
        self._verify_tls = verify_tls            # False = trust self-signed (local LAN devices)
        self._t = transport
        self._headers: dict[str, str] = {}
        self._params: dict[str, str] = {}
        atype = (auth or {}).get("type") or "none"
        if atype == "bearer":
            self._headers["Authorization"] = f"Bearer {env.get(auth.get('field') or '', '')}"
        elif atype == "basic":
            import base64
            user = env.get(auth.get("user_field") or "", "")
            pw = env.get(auth.get("pass_field") or "", "")
            self._headers["Authorization"] = \
                "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
        elif atype == "header":
            self._headers[auth.get("name") or "X-Api-Key"] = env.get(auth.get("field") or "", "")
        elif atype == "query":
            self._params[auth.get("name") or "api_key"] = env.get(auth.get("field") or "", "")

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        if not isinstance(path, str) or not path.startswith("/") or "://" in path or ".." in path:
            return {"error": "path must be a simple '/...' path on the integration's base URL"}
        merged = {**self._params, **(params or {})}
        _s, data = self._t("GET", f"{self.base_url}{path}", headers=self._headers,
                           params=merged or None, verify_tls=self._verify_tls)
        return data

    def probe(self) -> dict[str, Any]:
        path = self.probe_path or (self.read_paths[0] if self.read_paths else "")
        if not path:
            return {"ok": False,
                    "detail": "no probe path or read paths configured — edit the integration"}
        try:
            self.get(path)
            return {"ok": True, "detail": f"GET {path} ok"}
        except HttpError as e:
            # Surface the API's own error message (and a hint for auth failures) so a bad key or
            # wrong endpoint is diagnosable from the Test button, not just "HTTP 401".
            msg = ""
            try:
                import json as _json
                body = _json.loads(e.body or "{}")
                msg = body.get("message") or body.get("error") or body.get("error_description") or ""
            except Exception:
                msg = (e.body or "").strip()[:120]
            hint = " — the key was rejected; verify it's a valid key for this API" \
                if e.status in (401, 403) else ""
            return {"ok": False,
                    "detail": f"GET {path} → HTTP {e.status}" + (f": {msg}" if msg else "") + hint}
