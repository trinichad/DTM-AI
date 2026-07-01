"""Model router (D-3) — local-first model selection across providers.

Providers (all speak a NEUTRAL message history; each translates to its own wire format):
  - MockProvider   deterministic, dependency-free (dev/tests)
  - OllamaProvider local LLM via /api/chat (default; client data stays local)
  - OpenAIProvider OpenAI + any OpenAI-compatible endpoint via /v1/chat/completions
  - CodexProvider  OpenAI on the owner's ChatGPT plan (Codex OAuth, Responses API SSE — D-26)
  - ClaudeProvider Anthropic Messages API (our flagship cloud brain)

Neutral message shape (built by execution/agent.py):
  {"role":"system|user", "content": str}
  {"role":"assistant", "content": str, "tool_calls":[{"id","name","arguments":dict}]}
  {"role":"tool", "tool_call_id": str, "name": str, "content": str}

Cloud is opt-in: a cloud model is only AVAILABLE/selectable when its API key is configured
(adding the key via the secure credential form = the opt-in) and not hard-disabled by
MSPAI_ALLOW_CLOUD=0. Local stays the default. Providers take an injectable `transport` so the
request/response translation is unit-tested with no network.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .config import Config, get_config
from ..clients._http import HttpError, http_json, http_stream


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

    def chat_stream(self, messages, tools, model, emit) -> ChatResult:
        think = (self._script[0].get("thinking") if self._script else None)
        res = self.chat(messages, tools, model)
        if think:
            emit(think, "thinking")    # script entries may carry a reasoning stream (D-61)
        if res.content and not res.tool_calls:
            emit(res.content)          # no real streaming; deliver the whole answer once
        return res


# ── Ollama (local) ──────────────────────────────────────────────────────────
class OllamaProvider:
    name = "ollama"
    is_local = True

    def __init__(self, base_url: str, transport: Callable = http_json, num_ctx: int = 0,
                 stream_transport: Callable = http_stream) -> None:
        self.base_url = base_url.rstrip("/")
        self._t = transport
        self._st = stream_transport
        self.num_ctx = num_ctx  # Ollama context window (tokens); 0 = use Ollama's own default

    def _wire(self, messages: list[dict]) -> list[dict]:
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
        return wire

    def _payload(self, messages, tools, model, *, stream: bool) -> dict:
        payload = {"model": model, "messages": self._wire(messages), "stream": stream}
        if self.num_ctx:
            payload["options"] = {"num_ctx": self.num_ctx}   # widen the model's context window
        if tools:
            payload["tools"] = tools
        return payload

    def chat(self, messages, tools, model) -> ChatResult:
        payload = self._payload(messages, tools, model, stream=False)
        _s, data = self._t("POST", f"{self.base_url}/api/chat", json_body=payload, timeout=120)
        msg = (data or {}).get("message", {}) or {}
        calls = [{"name": tc.get("function", {}).get("name"),
                  "arguments": tc.get("function", {}).get("arguments", {})}
                 for tc in (msg.get("tool_calls") or [])]
        return ChatResult(msg.get("content", ""), calls, self.name, model, True)

    def chat_stream(self, messages, tools, model, emit) -> ChatResult:
        """Stream via Ollama's newline-delimited JSON. Each line carries a message.content delta;
        REASONING models (e.g. qwen3.5) stream their chain-of-thought in a separate `thinking`
        field first — emit that on the 'thinking' channel so the UI shows live progress, but keep it
        OUT of the returned content (only the real answer is persisted)."""
        payload = self._payload(messages, tools, model, stream=True)
        content, calls = "", []
        for line in self._st("POST", f"{self.base_url}/api/chat", json_body=payload, timeout=120):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message") or {}
            think = msg.get("thinking")
            if think:
                emit(think, "thinking")
            piece = msg.get("content") or ""
            if piece:
                content += piece
                emit(piece, "content")
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                calls.append({"name": fn.get("name"), "arguments": fn.get("arguments", {})})
        return ChatResult(content, calls, self.name, model, True)


# ── OpenAI (+ OpenAI-compatible) ────────────────────────────────────────────
class OpenAIProvider:
    name = "openai"
    is_local = False

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1",
                 transport: Callable = http_json, name: str = "openai", is_local: bool = False,
                 stream_transport: Callable = http_stream) -> None:
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self._t = transport
        self._st = stream_transport
        self.name = name
        self.is_local = is_local

    def _wire(self, messages: list[dict]) -> list[dict]:
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
        return wire

    def _payload(self, messages, tools, model, *, stream: bool) -> dict:
        payload: dict[str, Any] = {"model": model, "messages": self._wire(messages)}
        if stream:
            payload["stream"] = True
        if tools:
            payload["tools"] = tools
        return payload

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    def chat(self, messages, tools, model) -> ChatResult:
        payload = self._payload(messages, tools, model, stream=False)
        _s, data = self._t("POST", f"{self.base_url}/chat/completions", json_body=payload,
                           headers=self._headers, timeout=120)
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

    def chat_stream(self, messages, tools, model, emit) -> ChatResult:
        """Real SSE streaming over /chat/completions (stream=true). Content deltas emit live;
        tool-call deltas arrive fragmented (id/name in the first chunk, arguments as a stream of
        partial-JSON strings) and are accumulated per `index`, then parsed at the end. Some
        OpenAI-compatible reasoning endpoints also stream `delta.reasoning_content` — surfaced on the
        'thinking' channel like the other providers, kept out of the returned content."""
        payload = self._payload(messages, tools, model, stream=True)
        content = ""
        tc_acc: dict[int, dict[str, Any]] = {}   # index -> {id, name, args (partial-JSON buffer)}
        for line in self._st("POST", f"{self.base_url}/chat/completions", json_body=payload,
                             timeout=120, headers=self._headers):
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = ((obj.get("choices") or [{}])[0] or {}).get("delta") or {}
            piece = delta.get("content")
            if piece:
                content += piece
                emit(piece)
            think = delta.get("reasoning_content")
            if think:
                emit(think, "thinking")
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tc_acc.setdefault(idx, {"id": None, "name": None, "args": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
        calls = []
        for _idx in sorted(tc_acc):
            slot = tc_acc[_idx]
            try:
                args = json.loads(slot["args"]) if slot["args"].strip() else {}
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": slot["id"], "name": slot["name"], "arguments": args})
        return ChatResult(content, calls, self.name, model, self.is_local)


# ── OpenAI on the ChatGPT plan (Codex OAuth, D-26) ──────────────────────────
class CodexProvider:
    """ChatGPT-subscription access via the Codex backend (Responses API over SSE).

    No API key: `tokens` is a zero-arg callable returning (access_token, account_id),
    normally codex_auth.token_source(cfg) which auto-refreshes near expiry. The backend
    REQUIRES stream=true, so chat() is chat_stream() with a no-op emitter.
    SOP: architecture/openai-codex.md.
    """
    name = "openai-codex"
    is_local = False

    def __init__(self, tokens: Callable[[], tuple[str, str]],
                 base_url: str = "https://chatgpt.com/backend-api/codex",
                 stream_transport: Callable = http_stream) -> None:
        self._tokens = tokens
        self.base_url = base_url.rstrip("/")
        self._st = stream_transport

    def _build_body(self, messages, tools, model) -> dict:
        items: list[dict[str, Any]] = []
        for m in messages:
            r = m.get("role")
            if r == "system":
                continue                      # system text rides in `instructions`
            if r == "user":
                items.append({"type": "message", "role": "user",
                              "content": [{"type": "input_text", "text": m.get("content", "")}]})
            elif r == "assistant":
                if m.get("content"):
                    items.append({"type": "message", "role": "assistant",
                                  "content": [{"type": "output_text", "text": m["content"]}]})
                for c in m.get("tool_calls") or []:
                    items.append({"type": "function_call", "name": c["name"],
                                  "arguments": json.dumps(c["arguments"]),
                                  "call_id": c.get("id") or f"call_{uuid.uuid4().hex[:16]}"})
            elif r == "tool":
                items.append({"type": "function_call_output", "call_id": m.get("tool_call_id"),
                              "output": m.get("content", "")})
        body: dict[str, Any] = {
            "model": model,
            "instructions": _system_text(messages) or "You are a helpful assistant.",
            "input": items,
            "tools": [{"type": "function", "name": t["function"]["name"],
                       "description": t["function"].get("description", ""),
                       "parameters": t["function"]["parameters"], "strict": False}
                      for t in (tools or [])],
            "tool_choice": "auto", "parallel_tool_calls": False,
            "store": False, "stream": True,   # backend rejects stream=false
            # Ask the reasoning model for a live summary of its thinking (D-100). Without this the
            # backend emits NO response.reasoning_summary_text.delta events, so the UI's Reasoning
            # panel stays empty and the agent looks like it thinks silently. Display-only — never
            # persisted, never round-tripped back as input (SOP allows function calls w/o reasoning).
            "reasoning": {"summary": "auto"},
        }
        return body

    def chat_stream(self, messages, tools, model, emit) -> ChatResult:
        access, account = self._tokens()
        headers = {"Authorization": f"Bearer {access}", "chatgpt-account-id": account,
                   "OpenAI-Beta": "responses=experimental", "originator": "codex_cli_rs",
                   "session_id": str(uuid.uuid4())}
        body = self._build_body(messages, tools, model)
        content, calls = "", []
        for line in self._st("POST", f"{self.base_url}/responses", json_body=body, timeout=180,
                             headers=headers):
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            etype = ev.get("type", "")
            if etype == "response.output_text.delta":
                piece = ev.get("delta", "")
                if piece:
                    content += piece
                    emit(piece)
            elif etype == "response.reasoning_summary_text.delta":
                think = ev.get("delta", "")
                if think:
                    emit(think, "thinking")   # live progress only; never persisted
            elif etype == "response.output_item.done":
                item = ev.get("item") or {}
                if item.get("type") == "function_call":
                    try:
                        args = json.loads(item.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    calls.append({"id": item.get("call_id"), "name": item.get("name"),
                                  "arguments": args})
            elif etype == "response.failed":
                err = ((ev.get("response") or {}).get("error") or {}).get("message", "response.failed")
                raise HttpError(502, f"codex backend: {err}")
        return ChatResult(content, calls, self.name, model, False)

    def chat(self, messages, tools, model) -> ChatResult:
        return self.chat_stream(messages, tools, model, lambda *_a, **_k: None)


# ── Claude (Anthropic Messages API) ─────────────────────────────────────────
class ClaudeProvider:
    name = "claude"
    is_local = False

    def __init__(self, api_key: str, transport: Callable = http_json,
                 base_url: str = "https://api.anthropic.com/v1",
                 stream_transport: Callable = http_stream) -> None:
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self._t = transport
        self._st = stream_transport

    def _build_body(self, messages, tools, model, *, stream: bool) -> dict:
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
        if stream:
            body["stream"] = True
        # Prompt caching (D-113): the system prompt (~2k tok) and — far bigger — the tool list
        # (~28k tok for the 124 enabled tools) are an IDENTICAL prefix on every round of every turn.
        # Anthropic bills that at full input price each call unless we mark a cache breakpoint, so
        # without this a multi-round turn re-pays ~30k tokens per round. A breakpoint on the system
        # block and on the LAST tool caches the whole stable prefix (tools → system are processed
        # before the messages), served at ~10% cost + lower latency on subsequent calls. GA feature,
        # no beta header. Safe on a miss (first call just writes the cache).
        if sys_text:
            body["system"] = [{"type": "text", "text": sys_text,
                               "cache_control": {"type": "ephemeral"}}]
        if tools:
            tool_defs = [{"name": t["function"]["name"], "description": t["function"]["description"],
                          "input_schema": t["function"]["parameters"]} for t in tools]
            if tool_defs:
                tool_defs[-1]["cache_control"] = {"type": "ephemeral"}
            body["tools"] = tool_defs
        return body

    @property
    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._key, "anthropic-version": "2023-06-01"}

    def chat(self, messages, tools, model) -> ChatResult:
        body = self._build_body(messages, tools, model, stream=False)
        _s, data = self._t("POST", f"{self.base_url}/messages", json_body=body, timeout=120,
                           headers=self._headers)
        content, calls = "", []
        for block in (data or {}).get("content", []) or []:
            if block.get("type") == "text":
                content += block.get("text", "")
            elif block.get("type") == "tool_use":
                calls.append({"id": block.get("id"), "name": block.get("name"),
                              "arguments": block.get("input", {})})
        return ChatResult(content, calls, self.name, model, False)

    def chat_stream(self, messages, tools, model, emit) -> ChatResult:
        """Stream Anthropic SSE: text_delta chunks emit live; tool_use input_json_delta is
        accumulated per content block and parsed at block stop."""
        body = self._build_body(messages, tools, model, stream=True)
        content = ""
        calls: list[dict[str, Any]] = []
        blocks: dict[int, dict[str, Any]] = {}   # index -> {type, id?, name?, json_buf}
        for line in self._st("POST", f"{self.base_url}/messages", json_body=body, timeout=120,
                             headers=self._headers):
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            etype = ev.get("type")
            if etype == "content_block_start":
                blk = ev.get("content_block") or {}
                blocks[ev.get("index", 0)] = {"type": blk.get("type"), "id": blk.get("id"),
                                              "name": blk.get("name"), "json": ""}
            elif etype == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta":
                    piece = d.get("text", "")
                    if piece:
                        content += piece
                        emit(piece)
                elif d.get("type") == "thinking_delta":
                    # extended-thinking models stream reasoning blocks — same uniform channel
                    if d.get("thinking"):
                        emit(d.get("thinking", ""), "thinking")
                elif d.get("type") == "input_json_delta":
                    b = blocks.get(ev.get("index", 0))
                    if b is not None:
                        b["json"] += d.get("partial_json", "")
            elif etype == "content_block_stop":
                b = blocks.get(ev.get("index", 0))
                if b and b.get("type") == "tool_use":
                    try:
                        args = json.loads(b["json"]) if b["json"].strip() else {}
                    except json.JSONDecodeError:
                        args = {}
                    calls.append({"id": b.get("id"), "name": b.get("name"), "arguments": args})
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
    # ChatGPT-plan OAuth (D-26): the refresh token is the opt-in "key". Only gpt-5.5 is
    # accepted for subscription auth (verified live) — don't mirror the API-key catalog.
    "openai-codex": ("OPENAI_CODEX_REFRESH_TOKEN", "OpenAI (ChatGPT)", [
        ("gpt-5.5", "GPT-5.5 (ChatGPT plan)"),
    ]),
}

# Per-model context window in TOKENS — drives the chat context meter and the history budget.
# gpt-5.5 over the codex/ChatGPT-plan path is 400K (the raw API is 1M); Claude 4.x is 200K;
# GPT-4o is 128K. The LOCAL model's window is its configured num_ctx (MSPAI_OLLAMA_NUM_CTX), not a
# fixed number — a small local window is exactly why cloud is preferred for long work.
MODEL_CONTEXT_TOKENS = {
    "gpt-5.5": 400_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "claude-opus-4-8": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}
_DEFAULT_CLOUD_CONTEXT = 128_000


class ModelRouter:
    def __init__(self, cfg: Optional[Config] = None) -> None:
        self.cfg = cfg or get_config()
        self.local_model = self.cfg.get("MSPAI_LOCAL_MODEL", "llama3.1")
        self.ollama_url = self.cfg.get("MSPAI_OLLAMA_URL", "http://localhost:11434")
        self.ollama_num_ctx = self.cfg.int("MSPAI_OLLAMA_NUM_CTX", 16384)  # local model context window (tokens)
        self.history_msgs = self.cfg.int("MSPAI_MAX_HISTORY_MSGS", 40)
        # history char budget is MODEL-AWARE: a local model is bounded by its num_ctx, while a cloud
        # model has a much larger window. MSPAI_MAX_HISTORY_CHARS (>0) forces one value for all models.
        self._history_override = self.cfg.int("MSPAI_MAX_HISTORY_CHARS", 0)
        self._cloud_history = self.cfg.int("MSPAI_CLOUD_HISTORY_CHARS", 240000)  # cost cap (~60k tok)
        self._allow_mock_fallback = self.cfg.get("MSPAI_ENV", "dev") != "prod"

    def context_tokens(self, provider_name: str, model: Optional[str] = None) -> int:
        """The model's context WINDOW in tokens (for the chat meter). Local = its num_ctx."""
        if provider_name == "ollama":
            return self.ollama_num_ctx or 16384
        return MODEL_CONTEXT_TOKENS.get(model or "", _DEFAULT_CLOUD_CONTEXT)

    def budget_for(self, provider_name: str, model: Optional[str] = None) -> int:
        """Chars of prior conversation to send. Model-aware (D-50): a big-window cloud model gets a
        proportionally larger history budget so the window is actually used, bounded by a cost cap
        (MSPAI_CLOUD_HISTORY_CHARS); the local model is bounded by its num_ctx (~4 chars/token)."""
        if self._history_override > 0:
            return self._history_override
        if provider_name == "ollama":
            return max(4000, self.ollama_num_ctx * 2)   # ~4 chars/token, reserve ~half the window
        win_chars = self.context_tokens(provider_name, model) * 4
        return min(int(win_chars * 0.5), self._cloud_history)   # up to half the window, capped

    @property
    def history_chars(self) -> int:
        return self.budget_for("ollama")

    def cloud_allowed(self) -> bool:
        # allowed unless explicitly disabled; adding a key is the per-provider opt-in
        return self.cfg.get("MSPAI_ALLOW_CLOUD", "1") != "0"

    def _model_entry(self, prov: str, mid: str, label: str, *, local: bool, default: bool,
                     available: bool = True) -> dict[str, Any]:
        toks = self.context_tokens(prov, mid)
        return {"id": f"{prov}:{mid}", "provider": prov, "model": mid, "label": label,
                "local": local, "default": default, "available": available,
                "context_tokens": toks, "context_chars": toks * 4}   # window (not the send budget)

    def available_models(self) -> list[dict[str, Any]]:
        out = [self._model_entry("ollama", self.local_model, f"{self.local_model} (local)",
                                 local=True, default=True)]
        if self.cloud_allowed():
            for prov, (key, label, models) in CLOUD_CATALOG.items():
                if self.cfg.present(key):
                    for mid, mlabel in models:
                        out.append(self._model_entry(prov, mid, mlabel, local=False, default=False))
        return out

    def catalog_models(self) -> list[dict[str, Any]]:
        """The FULL model catalog (local + every cloud model), each with an `available` flag
        (local always; cloud iff cloud_allowed AND the key is present). Used by the per-agent
        brain picker so a Claude brain can be PRE-ASSIGNED before the API key exists — it simply
        goes live the moment the key lands. available_models() (the chat dropdown) lists only the
        runnable subset."""
        out = [{"id": f"ollama:{self.local_model}", "provider": "ollama", "model": self.local_model,
                "label": f"{self.local_model} (local)", "local": True, "available": True}]
        cloud_ok = self.cloud_allowed()
        for prov, (key, label, models) in CLOUD_CATALOG.items():
            keyed = self.cfg.present(key)
            for mid, mlabel in models:
                out.append({"id": f"{prov}:{mid}", "provider": prov, "model": mid, "label": mlabel,
                            "local": False, "available": bool(cloud_ok and keyed)})
        return out

    def is_catalog_model(self, model_id: str) -> bool:
        """True iff model_id is a known catalog id (keyed or not) — for brain validation."""
        return any(m["id"] == model_id for m in self.catalog_models())

    def resolve(self, model_id: Optional[str] = None):
        """Return (provider, model). Falls back to local for unknown/unauthorized cloud ids."""
        if model_id and ":" in model_id:
            prov, model = model_id.split(":", 1)
            if prov == "ollama":
                return OllamaProvider(self.ollama_url, num_ctx=self.ollama_num_ctx), model
            if prov == "anthropic" and self.cloud_allowed() and self.cfg.present("ANTHROPIC_API_KEY"):
                return ClaudeProvider(self.cfg.require("ANTHROPIC_API_KEY")), model
            if prov == "openai" and self.cloud_allowed() and self.cfg.present("OPENAI_API_KEY"):
                return OpenAIProvider(self.cfg.require("OPENAI_API_KEY")), model
            if prov == "openai-codex" and self.cloud_allowed() and self.cfg.present("OPENAI_CODEX_REFRESH_TOKEN"):
                from .codex_auth import token_source   # lazy: avoids config/credentials import cycle
                return CodexProvider(token_source(self.cfg)), model
        return OllamaProvider(self.ollama_url, num_ctx=self.ollama_num_ctx), self.local_model

    # back-compat: agent calls choose() when no explicit provider
    def choose(self, *, allow_cloud: bool = False, model_hint: Optional[str] = None):
        return self.resolve(model_hint)

    def mock(self, script: Optional[list[dict]] = None) -> MockProvider:
        return MockProvider(script)
