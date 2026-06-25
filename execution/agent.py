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
import threading
import time
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Optional

from .core.audit import AuditStore
from .core.context import ToolContext
from .core.dispatch import MAX_RESULT_CHARS, ApprovalGate, dispatch
from .core.registry import Registry
from .core.router import ChatResult, ModelRouter

SYSTEM_PROMPT = """You are MSP AI, the internal operations assistant for the MSP, an IT MSP.
You help MSP technicians inspect and operate client IT environments. Hard rules:
- You act ONLY through the tools provided this turn — never free-form shell, never a change without
  a tool. The tools you are given already reflect the owner's Capability Console: if a write/change
  tool is available to you, you MAY use it — every write is independently gated by the owner's
  approval policy, enforced outside your control, so you do not need to self-censor changes. If NO
  available tool can do what's asked, say so plainly (and offer to draft one with propose_tool);
  never invent a change, and never refuse a request merely because it changes something.
- When BUILDING or EXTENDING a capability (propose_tool / propose_connector_capability), explain
  what you're doing in PLAIN, NON-TECHNICAL language for a non-developer owner: what the new tool
  will let them do, in everyday terms, not API/cmdlet/code jargon. Mention the technical name once
  if useful, but lead with the plain-English "what this means for you". Keep it short.
- Never invent identifiers, counts, or facts. If a tool did not return it, say you don't know.
- Cite the tool(s) you used for every factual claim.
- You are bound to one client (tenant) per conversation; never reason across clients unless the
  tenant is explicitly "*" for a cross-client read.
Use the provided tools to answer. Prefer calling a tool over guessing.
- NEVER call the same tool over and over for a list of things. When you need the SAME action for
  many users / mailboxes / machines / devices / tickets, do it in ONE call: use the tool's own list
  parameter if it has one (e.g. m365_list_users `names`, m365_mfa_status `users`), otherwise use the
  `bulk` tool — bulk(tool="<name>", items=[{…}, {…}]) runs it once per item in a single call. This is
  required, not optional: repeating a tool wastes the tool-call budget and can hit the limit before
  you finish. Each item is still independently permission-checked, so bulk is always safe to prefer.
Before a multi-step task, call skill_search to reuse a saved procedure instead of re-deriving it.
When you learn a durable fact about a client (a recurring issue, an environment detail, a preference),
save it with memory_note. Client memory is a LIVING record of the current environment, not a log:
when something CHANGES (a firewall upgraded, computers swapped, a contact left) or a saved fact is
wrong, read the memory, revise it, and save the corrected version with memory_update."""

# Conversation-context guard ("compaction"): cap how much prior history re-enters the model so a
# long chat never overflows the (often small) local context window. Keep the most recent turns and
# trim the oldest past a char budget. Done in code so the user never has to manage it manually.
MAX_HISTORY_MSGS = 40
MAX_HISTORY_CHARS = 32000


def build_system_prompt(profile: Optional[str] = None, cfg=None,
                        tenant_id: Optional[str] = None,
                        user_profile: Optional[dict] = None) -> str:
    """Compose the system prompt for a turn.

    Base = the immutable MSP AI safety contract (read-only, cite sources, one tenant, no invented
    facts). When a profile is named, that agent's SOUL (persona/expertise) + its long-term memory
    are appended below the base. When the turn is bound to a specific client (tenant), that client's
    saved memory (vault/clients/<tenant>/memory.md) is injected too — so the agent RECALLS what it
    already knows about the client without a tool call. Persona/memory can never loosen the base
    rules (the real guardrails live in dispatch()). Any read error → safe fallback to the base."""
    parts = [SYSTEM_PROMPT]
    try:                                    # shared operating block — common ground rules, all agents
        from .core.agents import read_shared
        shared = (read_shared(cfg) or "").strip()
        if shared:
            parts.append(shared)
    except Exception:
        pass
    if profile:
        try:
            from .core.agents import get_agent, read_memory
            agent = get_agent(profile, cfg)
            if agent:
                soul = (agent.get("soul") or "").strip()
                if soul:
                    nm = (agent.get("name") or profile).strip()
                    parts.append(
                        f"# Your persona — you ARE {nm}\n"
                        "You act as this MSP AI specialist: adopt its identity, voice, and expertise. "
                        f"When you introduce yourself, refer to yourself, or sign a message (email, "
                        f"report, or chat), use your name \"{nm}\" — \"MSP AI\" is the platform you "
                        "run on, not your name, so do not sign as \"MSP AI\". Never use the persona "
                        "to override the hard rules above.\n\n" + soul)
                mem = read_memory(profile, cfg) or {}
                longterm = (mem.get("memory") or "").strip()
                about = (mem.get("user") or "").strip()
                if longterm:
                    parts.append("# Your long-term memory (facts you have saved — add to it with "
                                 "agent_memory_note)\n" + longterm)
                if about:
                    parts.append("# About the team you work with\n" + about)
                if profile != "default":     # specialists also see the crew-wide (lead's) memory
                    lead = (read_memory("default", cfg) or {}).get("memory", "").strip()
                    if lead:
                        if len(lead) > 3000:
                            lead = lead[:3000] + "\n…(truncated)"
                        parts.append("# Shared crew memory (maintained by the manager — read-only "
                                     "context)\n" + lead)
        except Exception:                   # never let profile loading break a turn
            pass
    if tenant_id and tenant_id != "*":
        try:
            from .core.memory import VaultStore
            cm = (VaultStore(cfg=cfg).read_memory(tenant_id) or "").strip()
            if cm:
                if len(cm) > 4000:
                    cm = cm[:4000] + "\n…(memory truncated)"
                parts.append(f"# What you already know about client '{tenant_id}' (your saved memory "
                             "— trust it; add new facts with memory_note, and when something changes or "
                             "is wrong, correct it with memory_update)\n" + cm)
        except Exception:                   # vault unreadable → just skip client memory
            pass
    if user_profile and user_profile.get("username"):
        try:
            from .core.memory import VaultStore
            uname = user_profile["username"]
            email = (user_profile.get("email") or "").strip()
            disp = (user_profile.get("name") or "").strip()
            block = [f"# The person you are talking to",
                     f"Signed in as: {disp or uname} (account: {uname}"
                     + (f", role {user_profile['role']}" if user_profile.get("role") else "") + ")."
                     + (f" Account email: {email}." if email else " No account email on file.")]
            block.append("\"email me\" / \"send it to me\" means their email — the account email "
                         "above unless their saved profile below states a preferred one.")
            um = (VaultStore(cfg=cfg).read_user_memory(uname) or "").strip()
            if um:
                if len(um) > 2500:
                    um = um[:2500] + "\n…(profile truncated)"
                block.append("Their saved profile (your memory about THIS person — read it before "
                             "acting on personal requests):\n" + um)
            block.append("Maintaining this profile: when they tell you something about themselves "
                         "(preferences, phone, schedule, a preferred email…), save it with "
                         "user_memory_note. If a new statement CONFLICTS with a stored fact — e.g. "
                         "they say 'email me at X' but a different address is stored — do NOT "
                         "silently overwrite: show the stored value and ask whether to UPDATE it, "
                         "KEEP it, or ADD the new one alongside. Then apply their choice with "
                         "user_memory_update (rewrite the profile) or user_memory_note (append).")
            parts.append("\n".join(block))
        except Exception:                   # never let user-profile loading break a turn
            pass
    return "\n\n".join(parts) if len(parts) > 1 else SYSTEM_PROMPT


def tool_payload(envelope: dict[str, Any], max_chars: int = MAX_RESULT_CHARS) -> str:
    """Serialize a tool result for the model, capping size WITHOUT silently hiding rows.

    A blind string-cut makes the model believe it saw the whole list — so on a large fleet it
    confidently reports machines as 'not found' when they were just past the cutoff. Instead, when
    the result is a long list we keep as many rows as fit and attach an explicit `_truncated` note
    telling the model the list is partial and to use a name/group filter. (Behavioral Rule #2.)"""
    blob = json.dumps(envelope, default=str)
    if len(blob) <= max_chars:
        return blob
    data = envelope.get("data")
    if isinstance(data, list) and len(data) > 1:
        keep = len(data)
        while keep > 0:
            trial = dict(envelope)
            trial["data"] = data[:keep]
            trial["_truncated"] = {
                "shown": keep, "total": len(data),
                "note": (f"PARTIAL RESULT — showing the first {keep} of {len(data)} items; the rest "
                         f"were dropped ONLY to fit the context limit (they are NOT absent). "
                         f"Re-calling with the SAME arguments returns the SAME page — do NOT loop. "
                         f"Present these rows to the user as a table, state that {len(data)} matched "
                         f"in total, and offer to narrow the query (a more specific name_contains/"
                         f"search) or export. Only a NARROWER query returns different rows.")}
            blob = json.dumps(trial, default=str)
            if len(blob) <= max_chars:
                return blob
            keep = int(keep * 0.8) if keep > 5 else keep - 1
    return blob[:max_chars]   # single oversized item — last-resort hard cut


def _data_preview(data: Any, cap: int = 6000) -> Optional[str]:
    """A short JSON preview of a tool's returned data, for showing inline in the transcript."""
    if data is None:
        return None
    try:
        blob = json.dumps(data, default=str)
    except Exception:
        blob = str(data)
    return blob[:cap] + "…" if len(blob) > cap else blob


def clean_history(history: Optional[list], max_msgs: int = MAX_HISTORY_MSGS,
                  max_chars: int = MAX_HISTORY_CHARS) -> list[dict[str, str]]:
    """Validate + bound caller-supplied chat history to user/assistant text turns.
    Limits are tunable (router reads MSPAI_MAX_HISTORY_MSGS / MSPAI_MAX_HISTORY_CHARS)."""
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
    stopped: bool = False        # the user interrupted this turn mid-flight (D-45)
    pending: Optional[dict] = None   # a write awaiting the user's approval, paused here (D-47)
    focus_client: Optional[str] = None   # the turn locked an all-clients chat to this client (D-52)
    reasoning: str = ""          # the model's thinking/reasoning stream — display only (D-61)


class _Interrupted(Exception):
    """Raised inside the streaming token callback to unwind a turn the user stopped."""


class Agent:
    def __init__(
        self,
        registry: Registry,
        audit: AuditStore,
        router: ModelRouter,
        gate: Optional[ApprovalGate] = None,
        max_rounds: int = 8,
        cfg=None,
    ) -> None:
        self.registry = registry
        self.audit = audit
        self.router = router
        self.gate = gate
        self.max_rounds = max_rounds
        self.cfg = cfg                       # for profile-aware system prompts (build_system_prompt)

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

    def _brain_for(self, profile: Optional[str], model_id: Optional[str]) -> Optional[str]:
        """Resolve which model to run on. An explicit model_id (the chat dropdown) always wins;
        otherwise fall back to the profile's ASSIGNED BRAIN (the `brain` sidecar). Without this a
        delegated agent run ignores its brain and silently lands on the local default."""
        if model_id or not profile:
            return model_id
        try:
            from .core.agents import get_brain_model
            return get_brain_model(profile, self.cfg) or None
        except Exception:                       # never let brain lookup break a turn
            return None

    def chat(
        self,
        ctx: ToolContext,
        message: str,
        *,
        provider=None,
        model_id: Optional[str] = None,
        approval_token: Optional[str] = None,
        history: Optional[list] = None,
        profile: Optional[str] = None,
    ) -> AgentTurn:
        # The agent always runs AS a profile (D-19); with none chosen it's the AtlasOps manager
        # ("default") — so its persona/identity (and email/report sign-off) is consistent across
        # the dashboard, Teams, the API and delegated runs, not the bare platform voice.
        profile = profile or "default"
        if provider is None:
            model_id = self._brain_for(profile, model_id)
            provider, model = self.router.resolve(model_id)
        else:
            model = (model_id.split(":", 1)[-1] if model_id and ":" in model_id
                     else (model_id or getattr(provider, "name", "mock")))

        ctx._meta.setdefault("profile", profile or "default")   # agent_memory_note → own MEMORY.md
        # The model THIS turn runs on, so tools (e.g. propose_tool) can draft with the SAME model
        # the user selected — not a fallback (D-53).
        ctx._meta["chat_model_id"] = f"{getattr(provider, 'name', 'ollama')}:{model}"
        tools = self._enabled_tool_specs()
        budget = (self.router.budget_for(getattr(provider, "name", "ollama"), model)
                  if hasattr(self.router, "budget_for")
                  else getattr(self.router, "history_chars", MAX_HISTORY_CHARS))
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(profile, self.cfg, ctx.tenant_id,
                                                user_profile=ctx._meta.get("user_profile"))}]
        messages.extend(clean_history(history, getattr(self.router, "history_msgs", MAX_HISTORY_MSGS), budget))
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
                                         "category": envelope.get("source"),
                                         "data": _data_preview(envelope.get("data"))})
                # A write that needs sign-off: PAUSE here exactly like chat_stream (D-47). Without
                # this the non-streaming loop would feed the "approval required" envelope back to
                # the model and keep firing the NEXT writes — piling up orphan approval rows that
                # only surface in the bell instead of one inline card at a time. Callers
                # (approval continuation, Teams, delegation) already read turn.pending.
                if envelope.get("status") == "pending_approval":
                    turn.pending = {"id": envelope.get("approval_id"), "tool": name,
                                    "tenant": ctx.tenant_id, "args": call["arguments"],
                                    "preview": envelope.get("approval_preview")}
                    turn.citations = citations
                    turn.answer = (f"I've prepared **{name}** and it needs your approval before it "
                                   f"runs. Review the action below and **Approve** to proceed (or "
                                   f"**Reject** to cancel) — I'll continue as soon as you decide.")
                    return turn
                payload = tool_payload(envelope)
                messages.append({"role": "tool", "tool_call_id": call["id"], "name": name, "content": payload})

        turn.answer = "Reached the tool-call limit without a final answer."
        turn.citations = citations
        return turn

    # Heartbeat cadence for a running tool (D-101). A long tool (e.g. an N+1 mailbox sweep) emits
    # nothing between tool_call and tool_result, so the UI sat on a dead spinner. Run dispatch on a
    # worker thread and emit elapsed-time progress every HEARTBEAT_S so it visibly keeps working.
    _HEARTBEAT_S = 1.5

    def _dispatch_heartbeat(self, *, name, ctx, args, approval_token, emit) -> dict:
        """dispatch() on a worker thread + a {type:tool_progress, elapsed_ms} heartbeat while it
        runs. dispatch is designed never to raise (failures come back as envelopes); guard anyway.
        emit is only ever called from THIS (the caller's) thread, so it stays single-writer."""
        box: dict = {}

        def _do():
            try:
                box["env"] = dispatch(
                    registry=self.registry, audit=self.audit, ctx=ctx,
                    name=name, args=args, approval_token=approval_token, gate=self.gate,
                    approvals=getattr(self, "approvals", None))
            except Exception as e:                       # belt-and-suspenders
                box["env"] = {"ok": False, "source": name, "error": f"{type(e).__name__}: {e}"}

        try:
            ctx._progress = None                     # clear stale batch progress before this run
        except Exception:
            pass
        th = threading.Thread(target=_do, daemon=True)
        th.start()
        t0 = time.monotonic()
        while True:
            th.join(timeout=self._HEARTBEAT_S)
            if not th.is_alive():
                break
            ev = {"type": "tool_progress", "name": name,
                  "elapsed_ms": int((time.monotonic() - t0) * 1000)}
            prog = getattr(ctx, "_progress", None)    # item-level batch progress (D-112), if any
            if prog:
                ev["progress"] = prog
            emit(ev)
        return box.get("env") or {"ok": False, "source": name, "error": "tool produced no result"}

    def _stream_provider(self, provider, messages, tools, model, emit_delta) -> ChatResult:
        try:
            return provider.chat_stream(messages, tools, model, emit_delta)
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            if getattr(self.router, "_allow_mock_fallback", False):
                return self.router.mock().chat_stream(messages, tools, model, emit_delta)
            raise RuntimeError(f"LLM provider '{provider.name}' unreachable: {e}") from e

    def chat_stream(
        self,
        ctx: ToolContext,
        message: str,
        emit,
        *,
        provider=None,
        model_id: Optional[str] = None,
        approval_token: Optional[str] = None,
        history: Optional[list] = None,
        profile: Optional[str] = None,
        should_stop=None,
    ) -> AgentTurn:
        """Same bounded loop as chat(), but streams progress via emit(event: dict):
          {"type":"tool_call","name","category"} · {"type":"tool_result","name","ok","source"}
          {"type":"delta","text"}   ← incremental answer tokens
        Returns the final AgentTurn (the caller persists it + emits the canonical 'answer').

        should_stop: optional 0-arg predicate. When it returns True the loop unwinds at the next
        safe point — between streamed tokens (so a long generation halts promptly) and before
        every tool dispatch (so a pending WRITE never fires after the user hit stop). The partial
        answer so far is preserved and turn.stopped is set."""
        profile = profile or "default"           # always run as a profile (see chat())
        if provider is None:
            model_id = self._brain_for(profile, model_id)
            provider, model = self.router.resolve(model_id)
        else:
            model = (model_id.split(":", 1)[-1] if model_id and ":" in model_id
                     else (model_id or getattr(provider, "name", "mock")))
        ctx._meta.setdefault("profile", profile or "default")   # agent_memory_note → own MEMORY.md
        # The model THIS turn runs on, so tools (e.g. propose_tool) can draft with the SAME model
        # the user selected — not a fallback (D-53).
        ctx._meta["chat_model_id"] = f"{getattr(provider, 'name', 'ollama')}:{model}"
        tools = self._enabled_tool_specs()
        budget = (self.router.budget_for(getattr(provider, "name", "ollama"), model)
                  if hasattr(self.router, "budget_for")
                  else getattr(self.router, "history_chars", MAX_HISTORY_CHARS))
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(profile, self.cfg, ctx.tenant_id,
                                                user_profile=ctx._meta.get("user_profile"))}]
        messages.extend(clean_history(history, getattr(self.router, "history_msgs", MAX_HISTORY_MSGS), budget))
        messages.append({"role": "user", "content": message})
        turn = AgentTurn(answer="", provider=getattr(provider, "name", "?"), model=model)
        citations: list[str] = []
        partial: list[str] = []                  # answer tokens streamed so far (for a clean stop)
        stop = should_stop or (lambda: False)

        def piece(text, kind="content"):
            # reasoning models stream a separate "thinking" channel before the answer; surface it
            # live as its own event AND keep it on the turn so the UI can show/persist it (D-61).
            # It is display-only: never part of `answer`, never fed back into model context.
            if stop():
                raise _Interrupted()             # unwind the provider stream promptly
            if kind == "thinking":
                if len(turn.reasoning) < 24_000:
                    turn.reasoning += text
            else:
                partial.append(text)
            emit({"type": "thinking" if kind == "thinking" else "delta", "text": text})

        def _stopped_turn() -> AgentTurn:
            note = "".join(partial).strip()
            turn.answer = (note + "  \n\n_(stopped)_") if note else "_(stopped before responding)_"
            turn.citations = citations
            turn.stopped = True
            return turn

        for rnd in range(self.max_rounds):
            if stop():
                return _stopped_turn()
            turn.rounds = rnd + 1
            if turn.reasoning and not turn.reasoning.endswith("\n\n"):
                turn.reasoning += "\n\n"         # visual break between rounds' reasoning (D-61)
            try:
                result = self._stream_provider(provider, messages, tools, model, piece)
            except _Interrupted:
                return _stopped_turn()
            if not result.tool_calls:
                turn.answer = result.content or ""
                turn.citations = citations
                return turn
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
                if stop():                       # never fire a queued tool (esp. a write) after stop
                    return _stopped_turn()
                emit({"type": "tool_call", "name": name})
                envelope = self._dispatch_heartbeat(
                    name=name, ctx=ctx, args=call["arguments"],
                    approval_token=approval_token, emit=emit)
                pending = envelope.get("status") == "pending_approval"
                if envelope["ok"]:
                    citations.append(f"{name}@{ctx.tenant_id}")
                # A write awaiting sign-off has NOT run or failed — record it as PENDING (ok=None),
                # not a red-✗ failure. resolve_pending flips the chip to ✓/✗ once decided (D-108).
                ev_ok = None if pending else envelope["ok"]
                te: dict[str, Any] = {"name": name, "ok": ev_ok, "category": envelope.get("source"),
                                      "data": _data_preview(envelope.get("data"))}
                if pending:
                    te["pending"] = True
                turn.tool_events.append(te)
                emit({"type": "tool_result", "name": name, "ok": ev_ok,
                      "pending": pending, "source": envelope.get("source")})
                # focus_client (D-52): lock an 'all clients' chat onto one client. Narrow THIS
                # turn's ctx so later per-client tools run scoped, and flag the turn so the chat
                # re-binds the conversation + the UI picker follows. Only narrows from '*'.
                if name == "focus_client" and envelope.get("ok"):
                    ft = (envelope.get("data") or {}).get("focused")
                    if ft and ctx.tenant_id == "*":
                        ctx.tenant_id = ft
                        turn.focus_client = ft
                        emit({"type": "client_locked", "tenant": ft})
                # A write that needs sign-off: PAUSE the turn here (D-47) rather than narrate a
                # failure. The proposed action is recorded; the user approves/rejects inline and the
                # turn resumes from the approval. Nothing further runs until they decide.
                if envelope.get("status") == "pending_approval":
                    turn.pending = {"id": envelope.get("approval_id"), "tool": name,
                                    "tenant": ctx.tenant_id, "args": call["arguments"],
                                    "preview": envelope.get("approval_preview")}
                    turn.citations = citations
                    turn.answer = (f"I've prepared **{name}** and it needs your approval before it "
                                   f"runs. Review the action below and **Approve** to proceed (or "
                                   f"**Reject** to cancel) — I'll continue as soon as you decide.")
                    emit({"type": "approval_required", "id": turn.pending["id"], "tool": name,
                          "args": call["arguments"], "tenant": ctx.tenant_id,
                          "preview": turn.pending["preview"]})
                    return turn
                payload = tool_payload(envelope)
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
