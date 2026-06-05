"""DTM AI MCP server — exposes the guarded tool registry to any MCP client (e.g. Hermes).

This is the FENCE (D-12): an external brain reaches DTM AI's tools only through here, and
every call still goes through dispatch() — so read-only/capability/approval/tenant/audit
guardrails apply no matter how autonomous the brain is.

Two transports, same JSON-RPC 2.0 handler (initialize / tools/list / tools/call / ping):

  • stdio (newline-delimited) — Hermes launches the server as a child process, ONE per tenant.
  • HTTP  (POST /mcp[/<tenant>]) — the server runs on the HOST (with creds, as `dtm-ai`) and a
    CONTAINERIZED Hermes connects over the network (D-17). The tenant is bound by the URL PATH,
    so each client gets its own URL and the same per-tenant isolation the stdio model gets from
    separate processes. Optional `DTM_MCP_TOKEN` → require `Authorization: Bearer <token>`.

Dependency-free (no `mcp` SDK needed) so it runs/tests anywhere; the official SDK can wrap the
same registry later if richer features are wanted.

Tenant binding: a request is bound to ONE tenant (stdio: the launch flag; HTTP: the URL path).
Any tenant_id in call arguments is ignored — a call cannot act outside its bound tenant.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Optional

from .runtime import build_agent, make_context

PROTOCOL_VERSION = "2024-11-05"


class DtmMcpServer:
    def __init__(self, tenant_id: str = "*", actor: str = "hermes") -> None:
        self.tenant_id = tenant_id
        self.actor = actor
        self.agent = build_agent()

    # ── JSON-RPC dispatch (pure; returns a response dict, or None for notifications) ──
    # `tenant` overrides the instance binding per request (HTTP path routing); stdio omits it.
    def handle(self, msg: dict[str, Any], tenant: Optional[str] = None) -> Optional[dict[str, Any]]:
        tenant = tenant or self.tenant_id
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            return self._ok(mid, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "dtm-ai", "version": "0.1.0",
                               "boundTenant": tenant},
            })
        if method in ("notifications/initialized", "initialized"):
            return None  # notification, no reply
        if method == "ping":
            return self._ok(mid, {})
        if method == "tools/list":
            return self._ok(mid, {"tools": self._tools()})
        if method == "tools/call":
            return self._call(mid, msg.get("params") or {}, tenant)
        return self._err(mid, -32601, f"method not found: {method}")

    def _tools(self) -> list[dict[str, Any]]:
        tools = []
        for t in self.agent.registry.all():
            if self.agent.audit.is_enabled(t.name, t.enabled_by_default):
                tools.append({"name": t.name, "description": t.description,
                              "inputSchema": t.parameters})
        return tools

    def _call(self, mid: Any, params: dict[str, Any], tenant: str) -> dict[str, Any]:
        from .core.dispatch import dispatch
        name = params.get("name", "")
        args = dict(params.get("arguments") or {})
        # Strip control keys; tenant cannot be overridden (fence), approval may be supplied.
        approval = args.pop("_approval_token", None)
        args.pop("tenant_id", None)
        args.pop("_tenant", None)
        ctx = make_context(tenant, actor=self.actor)
        env = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=ctx,
                       name=name, args=args, approval_token=approval, gate=self.agent.gate,
                       approvals=getattr(self.agent, "approvals", None))
        return self._ok(mid, {
            "content": [{"type": "text", "text": json.dumps(env, default=str)}],
            "isError": not env["ok"],
        })

    @staticmethod
    def _ok(mid: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _err(mid: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    # ── stdio loop ──
    def serve_stdio(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            response = self.handle(msg)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()


# ── HTTP transport ─────────────────────────────────────────────────────────────
# The server runs on the HOST (with creds) and a CONTAINERIZED Hermes connects over the
# network. Tenant is bound by the URL path (/mcp → "*", /mcp/<tenant> → that tenant), so
# the per-tenant fence is preserved without spawning a process per client.

def _path_tenant(path: str) -> Optional[str]:
    """Map a request path to its bound tenant, or None if the path is not an MCP endpoint.
    /mcp → "*" (all clients); /mcp/<tenant> → that tenant. Trailing slash tolerated."""
    path = path.rstrip("/") or "/"
    if path == "/mcp":
        return "*"
    if path.startswith("/mcp/"):
        seg = path[len("/mcp/"):]
        # one clean segment only — no traversal / sub-paths
        if seg and "/" not in seg:
            return seg
    return None


def make_http_handler(server: "DtmMcpServer", token: Optional[str]):
    from http import HTTPStatus
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):  # quiet by default (audit log is the record of truth)
            pass

        def _send(self, status: int, payload: Optional[dict]) -> None:
            body = b"" if payload is None else json.dumps(payload, default=str).encode()
            self.send_response(status)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _authorized(self) -> bool:
            if not token:
                return True
            auth = self.headers.get("Authorization", "")
            return auth.startswith("Bearer ") and auth[len("Bearer "):].strip() == token

        def do_GET(self):
            # health/liveness only — never exposes creds or tenant data
            if self.path.rstrip("/") in ("", "/health", "/mcp"):
                self._send(HTTPStatus.OK, {"ok": True, "service": "dtm-ai-mcp",
                                           "transport": "http"})
            else:
                self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

        def do_POST(self):
            tenant = _path_tenant(self.path)
            if tenant is None:
                self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                return
            if not self._authorized():
                self._send(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                msg = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                # JSON-RPC parse error
                self._send(HTTPStatus.OK, {"jsonrpc": "2.0", "id": None,
                                           "error": {"code": -32700, "message": "parse error"}})
                return
            response = server.handle(msg, tenant=tenant)
            if response is None:
                self._send(HTTPStatus.ACCEPTED, None)   # notification — no body
            else:
                self._send(HTTPStatus.OK, response)

    return Handler


def serve_http(host: str, port: int, actor: str = "hermes", token: Optional[str] = None):
    """Build the server (one shared agent) and serve MCP-over-HTTP until interrupted."""
    from http.server import ThreadingHTTPServer
    server = DtmMcpServer(tenant_id="*", actor=actor)
    httpd = ThreadingHTTPServer((host, port), make_http_handler(server, token))
    auth = "token-gated" if token else "OPEN (no DTM_MCP_TOKEN set)"
    print(f"DTM AI MCP (HTTP) on http://{host}:{port}/mcp[/<tenant>]  — {auth}  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    import argparse
    import os

    p = argparse.ArgumentParser(description="DTM AI MCP server")
    p.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                   help="stdio (Hermes launches per-tenant) or http (containerized Hermes connects)")
    p.add_argument("--tenant", default="*", help="[stdio] tenant this server is bound to")
    p.add_argument("--actor", default="hermes")
    p.add_argument("--host", default="127.0.0.1",
                   help="[http] bind address (use the docker bridge IP / host-gateway for containers)")
    p.add_argument("--port", type=int, default=8089, help="[http] listen port")
    args = p.parse_args()

    if args.transport == "http":
        # token via env so it never lands in the process list / Hermes config
        serve_http(args.host, args.port, actor=args.actor, token=os.environ.get("DTM_MCP_TOKEN"))
    else:
        DtmMcpServer(tenant_id=args.tenant, actor=args.actor).serve_stdio()
