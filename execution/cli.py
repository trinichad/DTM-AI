"""DTM AI CLI — verify the platform without the web stack.

Usage (from the project root):
    python3 -m execution.cli health --tenant acme
    python3 -m execution.cli tools
    python3 -m execution.cli chat --tenant acme "what is the platform status?"
    python3 -m execution.cli audit --tenant acme

Runs with zero external services: no Postgres (uses sqlite), no Ollama (the agent falls
back to a deterministic mock model in dev), no vendor creds (system_health needs none).
"""
from __future__ import annotations

import argparse
import json
import sys

from .core.context import ToolContext
from .core.credentials import status as cred_status
from .core.dispatch import dispatch
from .runtime import build_agent, make_context


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_health(args) -> int:
    agent = build_agent()
    ctx = make_context(args.tenant, actor=args.actor)
    env = dispatch(registry=agent.registry, audit=agent.audit, ctx=ctx, name="system_health")
    _print(env)
    return 0 if env["ok"] else 1


def cmd_tools(args) -> int:
    agent = build_agent()
    rows = [
        {"name": t.name, "category": t.category, "risk": t.risk_level,
         "requires_approval": t.requires_approval, "source": t.source,
         "enabled": agent.audit.is_enabled(t.name, t.enabled_by_default)}
        for t in agent.registry.all()
    ]
    _print({"count": len(rows), "tools": rows})
    return 0


def cmd_integrations(args) -> int:
    _print([
        {"integration": s.integration, "label": s.label, "configured": s.configured,
         "missing": s.missing, "fingerprints": s.fingerprints}
        for s in cred_status()
    ])
    return 0


def cmd_chat(args) -> int:
    agent = build_agent()
    ctx: ToolContext = make_context(args.tenant, actor=args.actor, allow_cloud=args.allow_cloud)
    turn = agent.chat(ctx, args.message, model_hint=args.model)
    _print({
        "answer": turn.answer, "citations": turn.citations,
        "tool_events": turn.tool_events, "provider": turn.provider,
        "model": turn.model, "rounds": turn.rounds,
    })
    return 0


def cmd_audit(args) -> int:
    agent = build_agent()
    _print(agent.audit.query(tenant_id=args.tenant, limit=args.limit))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="dtm-ai", description="DTM AI operations assistant (CLI)")
    p.add_argument("--actor", default="cli@dtm", help="actor identity for the audit log")
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("health", help="run the system_health tool")
    h.add_argument("--tenant", default="*")
    h.set_defaults(func=cmd_health)

    t = sub.add_parser("tools", help="list discovered tools")
    t.set_defaults(func=cmd_tools)

    i = sub.add_parser("integrations", help="show integration credential status (fingerprints only)")
    i.set_defaults(func=cmd_integrations)

    c = sub.add_parser("chat", help="chat with the agent")
    c.add_argument("message")
    c.add_argument("--tenant", default="*")
    c.add_argument("--model", default=None)
    c.add_argument("--allow-cloud", action="store_true")
    c.set_defaults(func=cmd_chat)

    a = sub.add_parser("audit", help="show recent audit-log entries")
    a.add_argument("--tenant", default=None)
    a.add_argument("--limit", type=int, default=20)
    a.set_defaults(func=cmd_audit)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
