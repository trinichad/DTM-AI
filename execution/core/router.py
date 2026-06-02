"""Model router (D-3) — local-first model selection across providers.

Providers (all speak a NEUTRAL message history; each translates to its own wire format):
  - MockProvider   deterministic, dependency-free (dev/tests)
  - OllamaProvider local LLM via /api/chat (default; client data stays local)
  - OpenAIProvider OpenAI + any OpenAI-compatible endpoint via /v1/chat/completions
  - ClaudeProvider Anthropic Messages API (our flagship cloud brain)

Neutral message shape (built by execution/agent.py):
  {"role":"system|user", "content": str}
  {"role":"assistant", "content": str, "tool_calls":[{"id","name","arguments":dict}]}
  {"role":"tool", "tool_call_id": str, "name": str, "content": str}

Cloud is opt-in: a cloud model is only AVAILABLE/selectable when its API key is configured
(adding the key via the secure credential form = the opt-in) and not hard-disabled by
DTM_ALLOW_CLOUD=0. Local stays the default. Providers take an injectable `transport` so the
request/response translation is unit-tested with no network.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .config import Config, get_config
from ..clients._http import HttpError, http_json


@dataclass
class ChatResult:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # [{id?,name,arguments}]
    provider: str = ""
    model: str = ""
    is_local: bool = True


# ── helpers to split the neutral history ────────────────────────────────────
def _system_text(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages if m.get("role") == "system")


# ── Mock ────────────────────────────────────────────────────────────────────
class MockProvider:
    name = "mock"
    is_local = True

    def __init__(self, script: Optional[list[dict]] = None) -> None:
        self._script = list(script or [])

    def chat(self, messages, tools, model) -> ChatResult:
        if self._script:
            s = self._script.pop(0)
            return ChatResult(s.get("content", ""), s.get("tool_calls", []), self.name, model, True)
        last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        return ChatResult(f"[mock:{model}] {last}", [], self.name, model, True)


# ── Ollama (local) ──────────────────────────────────────────────────────────
class OllamaProvider:
    name = "ollama"
    is_local = True

    def __init__(self, base_url: str, transport: Callable = http_json) -> None:
        self.base_url = base_url.rstrip("/")
        self._t = transport

    def chat(self, messages, tools, model) -> ChatResult:
        wire = []
        for m in messages:
            r = m.get("role")
            if r in ("system", "user"):
                wire.append({"role": r, "content": m.get("content", "")})
            elif r == "assistant":
                msg = {"role": "assistant", "content": m.get("content", "") or ""}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"function": {"name": c["name"], "arguments": c["arguments"]}}
                                         for c in m["tool_calls"]]
                wire.append(msg)
            elif r == "tool":
                wire.append({"role": "tool", "content": m.get("content", "")})
        payload = {"model": model, "messages": wire, "stream": False}
        if tools:
            payload["tools"] = tools
        _s, data = self._t("POST", f"{self.base_url}/api/chat", json_body=payload, timeout=120)
        msg = (data or {}).get("message", {}) or {}
        calls = [{"name": tc.get("function", {}).get("name"),
                  "arguments": tc.get("function", {}).get("arguments", {})}
                 for tc in (msg.get("tool_calls") or [])]
        return ChatResult(msg.get("content", ""), calls, self.name, model, True)


# ── OpenAI (+ OpenAI-compatible) ────────────────────────────────────────────
class OpenAIProvider:
    name = "openai"
    is_local = False

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1",
                 transport: Callable = http_json, name: str = "openai", is_local: bool = False) -> None:
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self._t = transport
        self.name = name
        self.is_local = is_local

    def chat(self, messages, tools, model) -> ChatResult:
        wire = []
        for m in messages:
            r = m.get("role")
            if r in ("system", "user"):
                wire.append({"role": r, "content": m.get("content", "")})
            elif r == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                          "function": {"name": c["name"],
                                                       "arguments": json.dumps(c["arguments"])}}
                                         for c in m["tool_calls"]]
                wire.append(msg)
            elif r == "tool":
                wire.append({"role": "tool", "tool_call_id": m.get("tool_call_id"),
                             "content": m.get("content", "")})
        payload: dict[str, Any] = {"model": model, "messages": wire}
        if tools:
            payload["tools"] = tools
        _s, data = self._t("POST", f"{self.base_url}/chat/completions", json_body=payload,
                           headers={"Authorization": f"Bearer {self._key}"}, timeout=120)
        choice = ((data or {}).get("choices") or [{}])[0].get("message", {}) or {}
        calls = []
        for tc in choice.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": tc.get("id"), "name": fn.get("name"), "arguments": args})
        return ChatResult(choice.get("content") or "", calls, self.name, model, self.is_local)


# ── Claude (Anthropic Messages API) ─────────────────────────────────────────
class ClaudeProvider:
    name = "claude"
    is_local = False

    def __init__(self, api_key: str, transport: Callable = http_json,
                 base_url: str = "https://api.anthropic.com/v1") -> None:
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self._t = transport

    def chat(self, messages, tools, model) -> ChatResult:
        sys_text = _system_text(messages)
        wire: list[dict[str, Any]] = []
        pending_tool_results: list[dict[str, Any]] = []

        def flush_tools():
            if pending_tool_results:
                wire.append({"role": "user", "content": list(pending_tool_results)})
                pending_tool_results.clear()

        for m in messages:
            r = m.get("role")
            if r == "system":
                continue
            if r == "user":
                flush_tools()
                wire.append({"role": "user", "content": [{"type": "text", "text": m.get("content", "")}]})
            elif r == "assistant":
                flush_tools()
                blocks: list[dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for c in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": c["id"], "name": c["name"],
                                   "input": c["arguments"]})
                wire.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            elif r == "tool":
                pending_tool_results.append({"type": "tool_result",
                                             "tool_use_id": m.get("tool_call_id"),
                                             "content": m.get("content", "")})
        flush_tools()

        body: dict[str, Any] = {"model": model, "max_tokens": 1500, "messages": wire}
        if sys_text:
            body["system"] = sys_text
        if tools:
            body["tools"] = [{"name": t["function"]["name"], "description": t["function"]["description"],
                              "input_schema": t["function"]["parameters"]} for t in tools]
        _s, data = self._t("POST", f"{self.base_url}/messages", json_body=body, timeout=120,
                           headers={"x-api-key": self._key, "anthropic-version": "2023-06-01"})
        content, calls = "", []
        for block in (data or {}).get("content", []) or []:
            if block.get("type") == "text":
                content += block.get("text", "")
            elif block.get("type") == "tool_use":
                calls.append({"id": block.get("id"), "name": block.get("name"),
                              "arguments": block.get("input", {})})
        return ChatResult(content, calls, self.name, model, False)


# ── model catalog + router ──────────────────────────────────────────────────
# (provider, key_env, [(model_id, label)])
CLOUD_CATALOG = {
    "anthropic": ("ANTHROPIC_API_KEY", "Claude", [
        ("claude-opus-4-8", "Claude Opus 4.8"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
    ]),
    "openai": ("OPENAI_API_KEY", "OpenAI", [
        ("gpt-4o", "GPT-4o"),
        ("gpt-4o-mini", "GPT-4o mini"),
    ]),
}


class ModelRouter:
    def __init__(self, cfg: Optional[Config] = None) -> None:
        self.cfg = cfg or get_config()
        self.local_model = self.cfg.get("DTM_LOCAL_MODEL", "llama3.1")
        self.ollama_url = self.cfg.get("DTM_OLLAMA_URL", "http://localhost:11434")
        self._allow_mock_fallback = self.cfg.get("DTM_ENV", "dev") != "prod"

    def cloud_allowed(self) -> bool:
        # allowed unless explicitly disabled; adding a key is the per-provider opt-in
        return self.cfg.get("DTM_ALLOW_CLOUD", "1") != "0"

    def available_models(self) -> list[dict[str, Any]]:
        out = [{"id": f"ollama:{self.local_model}", "provider": "ollama", "model": self.local_model,
                "label": f"{self.local_model} (local)", "local": True, "default": True}]
        if self.cloud_allowed():
            for prov, (key, label, models) in CLOUD_CATALOG.items():
                if self.cfg.present(key):
                    for mid, mlabel in models:
                        out.append({"id": f"{prov}:{mid}", "provider": prov, "model": mid,
                                    "label": mlabel, "local": False, "default": False})
        return out

    def resolve(self, model_id: Optional[str] = None):
        """Return (provider, model). Falls back to local for unknown/unauthorized cloud ids."""
        if model_id and ":" in model_id:
            prov, model = model_id.split(":", 1)
            if prov == "ollama":
                return OllamaProvider(self.ollama_url), model
            if prov == "anthropic" and self.cloud_allowed() and self.cfg.present("ANTHROPIC_API_KEY"):
                return ClaudeProvider(self.cfg.require("ANTHROPIC_API_KEY")), model
            if prov == "openai" and self.cloud_allowed() and self.cfg.present("OPENAI_API_KEY"):
                return OpenAIProvider(self.cfg.require("OPENAI_API_KEY")), model
        return OllamaProvider(self.ollama_url), self.local_model

    # back-compat: agent calls choose() when no explicit provider
    def choose(self, *, allow_cloud: bool = False, model_hint: Optional[str] = None):
        return self.resolve(model_hint)

    def mock(self, script: Optional[list[dict]] = None) -> MockProvider:
        return MockProvider(script)
