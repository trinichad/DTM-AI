"""Agent (N-layer / Navigation) — the bounded tool-calling loop.

Routes between the model (via ModelRouter) and the tools (via dispatch). It does NOT
do heavy work itself; it decides what tool to call and in what order, then lets the
deterministic tools run. Ported from Kaseya Link's loop with the guardrails moved into
dispatch() and model selection moved into the router.

Safety properties carried forward:
  - hard cap on tool-call rounds (default 8) — no infinite loops
  - tool results truncated before re-entering model context
  - every tool result flows back as a role:tool message
  - tools that fail return {"ok": false, "error": ...}; the loop feeds that to the model
Dev convenience: if the local LLM is unreachable, falls back to the MockProvider so the
platform is demonstrable with zero infrastructure (never in prod).
"""
from __future__ import annotations

import json
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Optional

from .core.audit import AuditStore
from .core.context import ToolContext
from .core.dispatch import MAX_RESULT_CHARS, ApprovalGate, dispatch
from .core.registry import Registry
from .core.router import ChatResult, ModelRouter

SYSTEM_PROMPT = """You are DTM AI, the internal operations assistant for DTM Consulting, an IT MSP.
You help DTM technicians inspect client IT environments. Hard rules:
- You are READ-ONLY. You never change client systems. If asked to make a change, decline and explain.
- Never invent identifiers, counts, or facts. If a tool did not return it, say you don't know.
- Cite the tool(s) you used for every factual claim.
- You are bound to one client (tenant) per conversation; never reason across clients unless the
  tenant is explicitly "*" for a cross-client read.
Use the provided tools to answer. Prefer calling a tool over guessing."""

# Conversation-context guard ("compaction"): cap how much prior history re-enters the model so a
# long chat never overflows the (often small) local context window. Keep the most recent turns and
# trim the oldest past a char budget. Done in code so the user never has to manage it manually.
MAX_HISTORY_MSGS = 30
MAX_HISTORY_CHARS = 16000


def clean_history(history: Optional[list], max_msgs: int = MAX_HISTORY_MSGS,
                  max_chars: int = MAX_HISTORY_CHARS) -> list[dict[str, str]]:
    """Validate + bound caller-supplied chat history to user/assistant text turns.
    Limits are tunable (router reads DTM_MAX_HISTORY_MSGS / DTM_MAX_HISTORY_CHARS)."""
    if not history:
        return []
    out: list[dict[str, str]] = []
    for h in history:
        if not isinstance(h, dict):
            continue
        role, content = h.get("role"), h.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content})
    out = out[-max_msgs:]
    total = sum(len(m["content"]) for m in out)
    while len(out) > 1 and total > max_chars:
        total -= len(out[0]["content"])
        out.pop(0)
    return out


@dataclass
class AgentTurn:
    answer: str
    citations: list[str] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    rounds: int = 0


class Agent:
    def __init__(
        self,
        registry: Registry,
        audit: AuditStore,
        router: ModelRouter,
        gate: Optional[ApprovalGate] = None,
        max_rounds: int = 8,
    ) -> None:
        self.registry = registry
        self.audit = audit
        self.router = router
        self.gate = gate
        self.max_rounds = max_rounds

    def _enabled_tool_specs(self) -> list[dict[str, Any]]:
        specs = []
        for t in self.registry.all():
            if self.audit.is_enabled(t.name, t.enabled_by_default):
                specs.append(t.to_schema())
        return specs

    def _call_provider(
        self, provider, messages: list[dict], tools: list[dict], model: str
    ) -> ChatResult:
        try:
            return provider.chat(messages, tools, model)
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            # Dev fallback: local LLM not running -> deterministic mock so the app still answers.
            if getattr(self.router, "_allow_mock_fallback", False):
                return self.router.mock().chat(messages, tools, model)
            raise RuntimeError(f"LLM provider '{provider.name}' unreachable: {e}") from e

    def chat(
        self,
        ctx: ToolContext,
        message: str,
        *,
        provider=None,
        model_id: Optional[str] = None,
        approval_token: Optional[str] = None,
        history: Optional[list] = None,
    ) -> AgentTurn:
        if provider is None:
            provider, model = self.router.resolve(model_id)
        else:
            model = (model_id.split(":", 1)[-1] if model_id and ":" in model_id
                     else (model_id or getattr(provider, "name", "mock")))

        tools = self._enabled_tool_specs()
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(clean_history(history, getattr(self.router, "history_msgs", MAX_HISTORY_MSGS),
                                      getattr(self.router, "history_chars", MAX_HISTORY_CHARS)))
        messages.append({"role": "user", "content": message})
        turn = AgentTurn(answer="", provider=getattr(provider, "name", "?"), model=model)
        citations: list[str] = []

        for rnd in range(self.max_rounds):
            turn.rounds = rnd + 1
            result = self._call_provider(provider, messages, tools, model)
            if not result.tool_calls:
                turn.answer = result.content or ""
                turn.citations = citations
                return turn

            # assign a stable id to each tool call (cloud providers require id pairing)
            calls = []
            for i, call in enumerate(result.tool_calls):
                raw_args = call.get("arguments", {})
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        raw_args = {}
                calls.append({"id": call.get("id") or f"call_{rnd}_{i}",
                              "name": call.get("name", ""), "arguments": raw_args})
            messages.append({"role": "assistant", "content": result.content or "", "tool_calls": calls})
            for call in calls:
                name = call["name"]
                envelope = dispatch(
                    registry=self.registry, audit=self.audit, ctx=ctx,
                    name=name, args=call["arguments"], approval_token=approval_token, gate=self.gate,
                    approvals=getattr(self, "approvals", None),
                )
                if envelope["ok"]:
                    citations.append(f"{name}@{ctx.tenant_id}")
                turn.tool_events.append({"name": name, "ok": envelope["ok"],
                                         "category": envelope.get("source")})
                payload = json.dumps(envelope, default=str)[:MAX_RESULT_CHARS]
                messages.append({"role": "tool", "tool_call_id": call["id"], "name": name, "content": payload})

        turn.answer = "Reached the tool-call limit without a final answer."
        turn.citations = citations
        return turn

    def summarize(self, history: Optional[list], *, model_id: Optional[str] = None) -> str:
        """Compact a conversation into a concise summary (no tools) so chat can continue with the
        key context preserved instead of oldest turns being dropped. Used by the UI 'Compact' button."""
        msgs = clean_history(history, max_msgs=200, max_chars=20000)
        if not msgs:
            return ""
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in msgs)[:12000]
        provider, model = self.router.resolve(model_id)
        prompt = [
            {"role": "system", "content": "You compress an IT-operations chat transcript. Produce a tight "
             "summary that preserves key facts, findings, identifiers (hostnames, counts, tenants), decisions, "
             "and any open threads, so the conversation can continue with full context. Use short bullet points; "
             "do not invent anything not present."},
            {"role": "user", "content": convo},
        ]
        result = self._call_provider(provider, prompt, [], model)
        return (result.content or "").strip()
