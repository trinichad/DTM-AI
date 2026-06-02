"""Model router (D-3) — the seam the Kaseya Link build lacked.

Local-first: tasks touching client data run on the local Ollama model by DEFAULT.
A cloud provider (Claude/OpenAI) is selected ONLY when all of these hold:
  - the task explicitly set allow_cloud=True (Behavioral Rule #5), AND
  - the global DTM_ALLOW_CLOUD kill switch is on, AND
  - the provider's API key is configured.
Otherwise the router stays local. This makes data-egress a deliberate, gated act.

Providers implement a tiny interface; the core ships:
  - MockProvider   deterministic, dependency-free — powers local dev + tests with no LLM.
  - OllamaProvider local LLM via urllib (no httpx needed in the core).
  - ClaudeProvider cloud, opt-in (lazy; only touched when explicitly routed to).
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from .config import Config, get_config


@dataclass
class ChatResult:
    content: str
    tool_calls: list[dict[str, Any]]          # [{"name": str, "arguments": dict}]
    provider: str
    model: str
    is_local: bool


class Provider(Protocol):
    name: str
    is_local: bool

    def chat(self, messages: list[dict], tools: list[dict], model: str) -> ChatResult: ...


# ── Mock (dev/tests; no external service) ───────────────────────────────────
class MockProvider:
    """Deterministic provider for local dev + unit tests.

    Optionally scripted: pass a queue of ChatResult-like dicts to replay (lets a test
    drive the agent loop through tool calls). With no script, it echoes the last user
    message so a chat round-trips with zero infrastructure.
    """

    name = "mock"
    is_local = True

    def __init__(self, script: Optional[list[dict]] = None) -> None:
        self._script = list(script or [])

    def chat(self, messages: list[dict], tools: list[dict], model: str) -> ChatResult:
        if self._script:
            step = self._script.pop(0)
            return ChatResult(
                content=step.get("content", ""),
                tool_calls=step.get("tool_calls", []),
                provider=self.name, model=model, is_local=True,
            )
        last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        return ChatResult(
            content=f"[mock:{model}] {last_user}", tool_calls=[],
            provider=self.name, model=model, is_local=True,
        )


# ── Ollama (local LLM) ──────────────────────────────────────────────────────
class OllamaProvider:
    name = "ollama"
    is_local = True

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def chat(self, messages: list[dict], tools: list[dict], model: str) -> ChatResult:
        payload = {"model": model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (trusted local URL)
            body = json.loads(resp.read().decode("utf-8"))
        msg = body.get("message", {}) or {}
        calls = []
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            calls.append({"name": fn.get("name"), "arguments": fn.get("arguments", {})})
        return ChatResult(
            content=msg.get("content", ""), tool_calls=calls,
            provider=self.name, model=model, is_local=True,
        )


# ── Claude (cloud, opt-in) ──────────────────────────────────────────────────
class ClaudeProvider:
    name = "claude"
    is_local = False

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def chat(self, messages: list[dict], tools: list[dict], model: str) -> ChatResult:
        # Implemented minimally via urllib; only ever reached when the router has already
        # confirmed allow_cloud + global flag + key. Tool-use wiring lands with Phase 2.
        raise NotImplementedError(
            "ClaudeProvider.chat is intentionally deferred to Phase 2 (cloud tool-use). "
            "The router correctly refuses to route here unless cloud is explicitly enabled."
        )


class ModelRouter:
    def __init__(self, cfg: Optional[Config] = None) -> None:
        self.cfg = cfg or get_config()
        self.local_model = self.cfg.get("DTM_LOCAL_MODEL", "llama3.1")
        self._ollama = OllamaProvider(self.cfg.get("DTM_OLLAMA_URL", "http://localhost:11434"))
        self._mock = MockProvider()
        # dev convenience: when no real LLM is reachable, fall back to mock so the app runs.
        self._allow_mock_fallback = self.cfg.get("DTM_ENV", "dev") != "prod"

    def cloud_enabled(self) -> bool:
        return self.cfg.bool("DTM_ALLOW_CLOUD", False)

    def choose(self, *, allow_cloud: bool, model_hint: Optional[str] = None) -> tuple[Provider, str]:
        """Pick (provider, model) for a task. Local unless cloud is explicitly unlocked."""
        wants_cloud = allow_cloud and self.cloud_enabled()
        if wants_cloud and self.cfg.present("ANTHROPIC_API_KEY"):
            model = model_hint or "claude-opus-4-8"
            return ClaudeProvider(self.cfg.require("ANTHROPIC_API_KEY")), model
        # default path: local
        model = model_hint or self.local_model
        if self._allow_mock_fallback and not self.cfg.present("DTM_OLLAMA_URL_FORCE"):
            # In dev we prefer the real Ollama if up, but the agent layer is responsible for
            # catching connection errors and retrying via mock. Return Ollama by default.
            return self._ollama, model
        return self._ollama, model

    def mock(self, script: Optional[list[dict]] = None) -> MockProvider:
        """Explicit mock provider (tests / offline demo)."""
        return MockProvider(script)
