"""DTM AI MCP server — exposes the guarded tool registry to any MCP client (e.g. Hermes).

This is the FENCE (D-12): an external brain reaches DTM AI's tools only through here, and
every call still goes through dispatch() — so read-only/capability/approval/tenant/audit
guardrails apply no matter how autonomous the brain is.

Protocol: JSON-RPC 2.0 over stdio (newline-delimited), MCP methods initialize / tools/list
/ tools/call / ping. Dependency-free (no `mcp` SDK needed) so it runs/tests anywhere; the
official SDK can wrap the same registry later if richer features are wanted.

Tenant binding: ONE server instance is bound to ONE tenant (the Hermes profile's client).
Any tenant_id in call arguments is ignored — the server cannot act outside its bound tenant.
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
    def handle(self, msg: dict[str, Any]) -> Optional[dict[str, Any]]:
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            return self._ok(mid, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "dtm-ai", "version": "0.1.0",
                               "boundTenant": self.tenant_id},
            })
        if method in ("notifications/initialized", "initialized"):
            return None  # notification, no reply
        if method == "ping":
            return self._ok(mid, {})
        if method == "tools/list":
            return self._ok(mid, {"tools": self._tools()})
        if method == "tools/call":
            return self._call(mid, msg.get("params") or {})
        return self._err(mid, -32601, f"method not found: {method}")

    def _tools(self) -> list[dict[str, Any]]:
        tools = []
        for t in self.agent.registry.all():
            if self.agent.audit.is_enabled(t.name, t.enabled_by_default):
                tools.append({"name": t.name, "description": t.description,
                              "inputSchema": t.parameters})
        return tools

    def _call(self, mid: Any, params: dict[str, Any]) -> dict[str, Any]:
        from .core.dispatch import dispatch
        name = params.get("name", "")
        args = dict(params.get("arguments") or {})
        # Strip control keys; tenant cannot be overridden (fence), approval may be supplied.
        approval = args.pop("_approval_token", None)
        args.pop("tenant_id", None)
        args.pop("_tenant", None)
        ctx = make_context(self.tenant_id, actor=self.actor)
        env = dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=ctx,
                       name=name, args=args, approval_token=approval, gate=self.agent.gate)
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


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="DTM AI MCP server (stdio)")
    p.add_argument("--tenant", default="*", help="tenant this server is bound to")
    p.add_argument("--actor", default="hermes")
    args = p.parse_args()
    DtmMcpServer(tenant_id=args.tenant, actor=args.actor).serve_stdio()
