"""Stdlib HTTP server: serves the dashboard + the JSON API. Binds 127.0.0.1 (behind nginx).

Run:  python3 -m execution.web            # http://127.0.0.1:8088
On first run it bootstraps an 'admin' user and prints a generated password (or uses
DTM_ADMIN_PASSWORD from .env).
"""
from __future__ import annotations

import json
import sys
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from ..core.config import get_config
from ..runtime import build_agent
from .api import SESSION_COOKIE, Api, Resp
from .auth import AuthStore, SessionSigner

_DASHBOARD = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"


def _make_handler(api: Api, signer: SessionSigner, secure_cookie: bool):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):  # quiet by default
            pass

        # ── helpers ──
        def _user(self) -> Optional[str]:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            morsel = cookie.get(SESSION_COOKIE)
            return signer.verify(morsel.value) if morsel else None

        def _send_json(self, resp: Resp) -> None:
            data = json.dumps(resp.payload, default=str).encode()
            self.send_response(resp.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            if resp.set_cookie is not None:
                self._cookie_header(resp.set_cookie, max_age=api.ttl * 60)
            if resp.clear_cookie:
                self._cookie_header("", max_age=0)
            self.end_headers()
            self.wfile.write(data)

        def _cookie_header(self, value: str, max_age: int) -> None:
            attrs = [f"{SESSION_COOKIE}={value}", "HttpOnly", "SameSite=Strict",
                     "Path=/", f"Max-Age={max_age}"]
            if secure_cookie:
                attrs.append("Secure")
            self.send_header("Set-Cookie", "; ".join(attrs))

        def _send_html(self) -> None:
            try:
                body = _DASHBOARD.read_bytes()
            except FileNotFoundError:
                body = b"<h1>DTM AI</h1><p>dashboard/index.html missing</p>"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return {}

        # ── verbs ──
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                self._send_json(api.handle("GET", parsed.path, query, {}, self._user()))
            else:
                self._send_html()  # SPA: any non-api path serves the dashboard

        def do_POST(self):
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                self._send_json(Resp(404, {"error": "not found"}))
                return
            self._send_json(api.handle("POST", parsed.path, {}, self._body(), self._user()))

        def do_DELETE(self):
            parsed = urlparse(self.path)
            self._send_json(api.handle("DELETE", parsed.path, {}, {}, self._user()))

    return Handler


def create_server(port: int = 8088, host: str = "127.0.0.1", db_path: Optional[Path] = None):
    cfg = get_config()
    agent = build_agent(cfg, db_path)
    auth = AuthStore(db_path)
    generated = auth.ensure_admin(cfg.get("DTM_ADMIN_PASSWORD"))
    if generated:
        print(f"\n  ┌─ DTM AI first-run ─────────────────────────────────────┐")
        print(f"  │  admin password (save this, shown once):               │")
        print(f"  │     {generated:<50}│")
        print(f"  └────────────────────────────────────────────────────────┘\n")
    signer = SessionSigner()
    api = Api(agent, auth, signer, session_ttl_min=cfg.int("DTM_SESSION_TTL_MIN", 720))
    handler = _make_handler(api, signer, secure_cookie=cfg.bool("DTM_COOKIE_SECURE", False))
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="DTM AI dashboard + API server")
    p.add_argument("--port", type=int, default=8088)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--hermes-skills-dir", default=None,
                   help="override where Hermes learned skills are read from (default ~/.hermes/skills)")
    args = p.parse_args(argv)
    if args.hermes_skills_dir:
        import os
        os.environ["DTM_HERMES_SKILLS_DIR"] = args.hermes_skills_dir
    srv = create_server(args.port, args.host)
    print(f"DTM AI listening on http://{args.host}:{args.port}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
