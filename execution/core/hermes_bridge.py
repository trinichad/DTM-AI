"""HermesBridge — relay a DTM AI chat turn to the Hermes brain (the IN channel, D-17).

When the chat "engine" is **Hermes**, DTM AI stops driving its own agent loop and instead
talks to the containerized Hermes over its OpenAI-compatible API server
(`POST {HERMES_API_BASE}/chat/completions`, Bearer `HERMES_API_KEY`). Hermes does the
thinking, memory, skills, and tool calls — reaching DTM AI's *own* guarded tools back
through the MCP fence, so every client-touching call is still audited via dispatch().

Why a network relay (not docker-exec): the DTM AI web service runs as the unprivileged
`dtm-ai` user, which is NOT in the docker group. With the Hermes container on host
networking, the api_server is reachable at 127.0.0.1:8642 — a plain HTTP call.

Session continuity: we pass the DTM AI `conversation_id` as `X-Hermes-Session-Id`, so the
brain keeps its own per-conversation memory in step with the DTM AI transcript.

Stdlib-only (urllib). Streaming SSE is parsed into normalized frames that match the shapes
`stream_chat` already emits: {"type":"delta","content":...}, {"type":"tool_call","name":...},
{"type":"tool_result","name":...}.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator, Optional

from .config import Config, get_config

DEFAULT_BASE = "http://127.0.0.1:8642/v1"
MODEL = "hermes-agent"

# friendly provider labels for the integrations card
_PROVIDER_LABELS = {
    "openai-codex": "OpenAI Codex",
    "openai": "OpenAI",
    "anthropic": "Claude",
    "ollama": "Ollama (local)",
    "openrouter": "OpenRouter",
    "nous-portal": "Nous Portal",
}


class HermesBridge:
    def __init__(self, cfg: Optional[Config] = None) -> None:
        self.cfg = cfg or get_config()
        self.base = (self.cfg.get("HERMES_API_BASE") or DEFAULT_BASE).rstrip("/")
        self.key = self.cfg.get("HERMES_API_KEY") or ""

    @property
    def available(self) -> bool:
        """The bridge is usable only if a key is configured. (We do not probe the network
        here — availability is a config fact; reachability surfaces as a clear error frame.)"""
        return bool(self.key)

    def _request(self, message: str, session_id: str, *, stream: bool) -> urllib.request.Request:
        body = {"model": MODEL, "stream": stream,
                "messages": [{"role": "user", "content": message}]}
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {self.key}",
                   "Accept": "text/event-stream" if stream else "application/json"}
        if session_id:
            headers["X-Hermes-Session-Id"] = session_id   # opt-in continuity → Hermes' own memory
        return urllib.request.Request(f"{self.base}/chat/completions",
                                      data=json.dumps(body).encode(), headers=headers, method="POST")

    # ── streaming: yields normalized frames, returns the full answer text ──
    def stream(self, message: str, session_id: str, emit: Callable[[dict], None],
               timeout: int = 300) -> str:
        """POST a streaming completion; call emit() for each delta/tool frame; return the
        accumulated answer. Raises on transport/HTTP error (caller frames it as an SSE error)."""
        req = self._request(message, session_id, stream=True)
        answer_parts: list[str] = []
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"Hermes API {e.code}: {detail}") from None
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"Hermes brain unreachable at {self.base}: {e}") from None

        for frame in _iter_sse(resp):
            event, data = frame
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if event and event.startswith("hermes.tool"):
                name = _clean_tool(obj.get("tool") or obj.get("label") or "tool")
                status = obj.get("status")
                if status == "running":
                    emit({"type": "tool_call", "name": name, "category": "read"})
                elif status == "completed":
                    emit({"type": "tool_result", "name": name, "ok": True})
                continue
            # default chat.completion.chunk → content delta
            delta = (((obj.get("choices") or [{}])[0]).get("delta") or {}).get("content")
            if delta:
                answer_parts.append(delta)
                emit({"type": "delta", "content": delta})
        return "".join(answer_parts)

    # ── non-streaming: returns the full answer text ──
    def complete(self, message: str, session_id: str, timeout: int = 300) -> str:
        req = self._request(message, session_id, stream=False)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            obj = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"Hermes API {e.code}: {detail}") from None
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"Hermes brain unreachable at {self.base}: {e}") from None
        return (((obj.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""

    # ── for the integrations card: which brain/model is Hermes driving ──
    def model_info(self) -> Optional[dict[str, str]]:
        """Read Hermes' configured model + provider from its config.yaml (best-effort)."""
        from pathlib import Path
        skills = self.cfg.get("DTM_HERMES_SKILLS_DIR")
        data_dir = (self.cfg.get("DTM_HERMES_DATA_DIR")
                    or (str(Path(skills).parent) if skills else None)
                    or str(Path.home() / ".hermes"))
        cfg_path = Path(data_dir) / "config.yaml"
        try:
            text = cfg_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        model = provider = None
        in_model = False
        for line in text.splitlines():
            if line.startswith("model:"):
                in_model = True
                continue
            if in_model:
                if line[:1] not in (" ", "\t"):   # dedent → left the model: block
                    break
                s = line.strip()
                if s.startswith("default:"):
                    model = s.split(":", 1)[1].strip().strip('"').strip("'")
                elif s.startswith("provider:"):
                    provider = s.split(":", 1)[1].strip().strip('"').strip("'")
        if not model and not provider:
            return None
        return {"model": model or "", "provider": provider or "",
                "provider_label": _PROVIDER_LABELS.get(provider or "", provider or "")}


def _clean_tool(name: str) -> str:
    """Strip Hermes' MCP namespacing for display. Hermes names tools mcp_<server>_<tool>; our
    servers are dtm_<client> (the client segment itself has an underscore), e.g.
    mcp_dtm_all_system_health → system_health, mcp_dtm_acme_kaseya_list_assets → kaseya_list_assets."""
    if name.startswith("mcp_dtm_"):
        rest = name[len("mcp_dtm_"):]          # "<client>_<tool>"
        return rest.split("_", 1)[1] if "_" in rest else rest
    if name.startswith("mcp_"):
        parts = name.split("_", 2)             # generic: drop mcp_<server>_
        return parts[2] if len(parts) == 3 else name
    return name


def _iter_sse(resp) -> Iterator[tuple[Optional[str], str]]:
    """Yield (event, data) per SSE frame. Accumulates `event:`/`data:` lines until a blank
    line dispatches the frame. Multiple data: lines are joined with newlines (SSE spec)."""
    event: Optional[str] = None
    data: list[str] = []
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = None, []
            continue
        if line.startswith(":"):
            continue  # comment/heartbeat
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:"):].lstrip())
    if data:
        yield event, "\n".join(data)
