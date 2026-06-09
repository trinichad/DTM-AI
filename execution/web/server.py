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
_VENDOR = Path(__file__).resolve().parents[2] / "dashboard" / "vendor"
_CTYPES = {".js": "application/javascript", ".css": "text/css", ".woff2": "font/woff2",
           ".woff": "font/woff", ".svg": "image/svg+xml", ".png": "image/png"}


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

        def _send_static(self, url_path: str) -> None:
            rel = url_path[len("/vendor/"):]
            target = (_VENDOR / rel).resolve()
            # confine to the vendor dir (no traversal)
            if ".." in rel or not str(target).startswith(str(_VENDOR.resolve())) or not target.is_file():
                self.send_response(HTTPStatus.NOT_FOUND); self.end_headers(); return
            body = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", _CTYPES.get(target.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)

        def _send_stream(self, events) -> None:
            """Stream Server-Sent Events. Connection: close + no Content-Length, so the browser's
            fetch() reader consumes frames until EOF. Each event is a `data: {json}\\n\\n` frame."""
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")   # tell nginx not to buffer the stream
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                for ev in events:
                    frame = f"data: {json.dumps(ev, default=str)}\n\n".encode()
                    self.wfile.write(frame)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass   # client navigated away mid-stream — fine

        def _send_html(self) -> None:
            try:
                body = _DASHBOARD.read_bytes()
            except FileNotFoundError:
                body = b"<h1>DTM AI</h1><p>dashboard/index.html missing</p>"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # The dashboard HTML is read fresh from disk on every request, so a UI edit is live
            # immediately — but only if the browser actually refetches it. BaseHTTPServer sends no
            # validators, so browsers heuristically cache and serve a stale page even on reload.
            # Forbid caching the shell outright; vendored assets under /vendor/ are still cacheable.
            self.send_header("Cache-Control", "no-store, must-revalidate")
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
            if parsed.path == "/ws/terminal":
                return self._ws_terminal()
            if parsed.path.startswith("/api/"):
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                self._send_json(api.handle("GET", parsed.path, query, {}, self._user()))
            elif parsed.path.startswith("/vendor/"):
                self._send_static(parsed.path)   # offline assets (tailwind/lucide/fonts)
            else:
                self._send_html()  # SPA: any non-api path serves the dashboard

        def do_POST(self):
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                self._send_json(Resp(404, {"error": "not found"}))
                return
            if parsed.path == "/api/chat/stream":          # streaming chat (SSE), auth-gated
                user = self._user()
                if not user:
                    self._send_json(Resp(401, {"error": "authentication required"}))
                    return
                self._send_stream(api.stream_chat(self._body(), user))
                return
            self._send_json(api.handle("POST", parsed.path, {}, self._body(), self._user()))

        def do_DELETE(self):
            parsed = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            self._send_json(api.handle("DELETE", parsed.path, query, {}, self._user()))

        # ── interactive PTY terminal over WebSocket (admin-only, audited — D-22) ──
        def _ws_terminal(self):
            from . import wsutil, pty_session
            from ..core.adminshell import terminal_enabled
            self.close_connection = True
            user = self._user()
            if not (user and api.auth.get_role(user) == "admin") or not terminal_enabled():
                self.send_response(HTTPStatus.FORBIDDEN); self.end_headers(); return
            if not wsutil.is_ws_upgrade(self.headers):
                self.send_response(HTTPStatus.BAD_REQUEST); self.end_headers(); return
            try:
                if not wsutil.handshake(self):
                    return
                api.agent.audit.record(actor=user, tenant_id="*", action="terminal", detail="pty open")
                pty_session.serve(self.connection)
            except Exception:
                pass   # disconnect / shell gone — pty_session always reaps the child

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
    p.add_argument("--agents-dir", default=None,
                   help="override the agent-profiles dir (default <vault>/agents; sets DTM_AGENTS_DIR)")
    p.add_argument("--hermes-skills-dir", default=None,
                   help=argparse.SUPPRESS)        # deprecated alias → legacy profile-location fallback
    args = p.parse_args(argv)
    import os
    if args.agents_dir:
        os.environ["DTM_AGENTS_DIR"] = args.agents_dir
    if args.hermes_skills_dir:
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
