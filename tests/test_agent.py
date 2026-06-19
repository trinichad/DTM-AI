"""Agent loop test — drives a scripted mock model through a real tool call.

Proves the full Navigation path: model requests a tool -> dispatch runs it (guarded) ->
result re-enters context -> model returns a final, cited answer.
"""
import json
import tempfile
import unittest
from pathlib import Path

from execution.agent import SYSTEM_PROMPT, Agent, build_system_prompt, clean_history, tool_payload
from execution.core.audit import AuditStore
from execution.core.context import ToolContext
from execution.core.dispatch import DenyAllApprovals
from execution.core.registry import Registry
from execution.core.router import ChatResult, ModelRouter
from execution.core.config import Config


class ToolPayload(unittest.TestCase):
    def test_small_result_passes_through(self):
        env = {"ok": True, "data": [1, 2, 3]}
        self.assertEqual(json.loads(tool_payload(env)), env)

    def test_large_list_is_capped_and_flagged_not_silently_cut(self):
        # Regression: a blind cut made the model think it saw the whole fleet -> false 'not found'.
        env = {"ok": True, "source": "kaseya",
               "data": [{"AssetName": f"m{i}.acme.local", "AgentId": i, "pad": "x" * 60}
                        for i in range(2000)]}
        out = tool_payload(env)
        self.assertLessEqual(len(out), 20_000)
        obj = json.loads(out)                                  # still valid JSON
        self.assertIn("_truncated", obj)                       # model is TOLD it's partial
        self.assertEqual(obj["_truncated"]["total"], 2000)
        self.assertLess(obj["_truncated"]["shown"], 2000)
        self.assertIn("name_contains", obj["_truncated"]["note"])


class _Recorder:
    """A provider that records the messages it was handed and answers with no tool calls."""
    name = "rec"

    def __init__(self):
        self.seen = None

    def chat(self, messages, tools, model):
        self.seen = messages
        return ChatResult("ok", [], self.name, model, True)


def _cfg() -> Config:
    d = tempfile.mkdtemp()
    p = Path(d) / ".env"
    p.write_text("MSPAI_ENV=dev\n", encoding="utf-8")
    p.chmod(0o600)
    return Config(env_path=p)


class AgentLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = AuditStore(Path(self.tmp.name) / "a.db")
        self.agent = Agent(Registry(), self.audit, ModelRouter(_cfg()), gate=DenyAllApprovals())

    def tearDown(self):
        self.audit.close()
        self.tmp.cleanup()

    def test_tool_call_then_cited_answer(self):
        script = [
            {"content": "", "tool_calls": [{"name": "system_health", "arguments": {}}]},
            {"content": "The platform is healthy; 0 integrations configured."},
        ]
        provider = self.agent.router.mock(script)
        ctx = ToolContext(tenant_id="acme", actor="tester")
        turn = self.agent.chat(ctx, "is everything ok?", provider=provider)

        self.assertEqual(turn.answer, "The platform is healthy; 0 integrations configured.")
        self.assertIn("system_health@acme", turn.citations)
        self.assertEqual(len(turn.tool_events), 1)
        self.assertTrue(turn.tool_events[0]["ok"])
        self.assertEqual(turn.rounds, 2)

    def test_loop_is_bounded(self):
        # a model that ALWAYS asks for a tool must still terminate at max_rounds
        forever = [{"content": "", "tool_calls": [{"name": "system_health", "arguments": {}}]}] * 50
        provider = self.agent.router.mock(forever)
        ctx = ToolContext(tenant_id="acme", actor="tester")
        turn = self.agent.chat(ctx, "loop forever", provider=provider)
        self.assertLessEqual(turn.rounds, self.agent.max_rounds)
        self.assertIn("limit", turn.answer.lower())


    def test_reasoning_stream_is_captured_and_kept_out_of_the_answer(self):
        # D-61: the 'thinking' channel lands on turn.reasoning (display only), streams as
        # 'thinking' events, and never leaks into the answer text.
        script = [{"thinking": "the user wants a health check; system_health fits",
                   "content": "", "tool_calls": [{"name": "system_health", "arguments": {}}]},
                  {"thinking": "results look fine, summarize",
                   "content": "All healthy."}]
        provider = self.agent.router.mock(script)
        ctx = ToolContext(tenant_id="acme", actor="t")
        events = []
        turn = self.agent.chat_stream(ctx, "health?", lambda e: events.append(e),
                                      provider=provider)
        self.assertEqual(turn.answer, "All healthy.")
        self.assertIn("system_health fits", turn.reasoning)
        self.assertIn("summarize", turn.reasoning)
        self.assertNotIn("summarize", turn.answer)
        thinks = [e["text"] for e in events if e.get("type") == "thinking"]
        self.assertEqual(len(thinks), 2)                   # streamed live, one per round

    def test_stop_unwinds_before_any_tool_runs(self):
        # D-45: if the user stops, no queued tool (esp. a write) may execute.
        script = [{"content": "", "tool_calls": [{"name": "system_health", "arguments": {}}]},
                  {"content": "done"}]
        provider = self.agent.router.mock(script)
        ctx = ToolContext(tenant_id="acme", actor="t")
        turn = self.agent.chat_stream(ctx, "go", lambda e: None,
                                      provider=provider, should_stop=lambda: True)
        self.assertTrue(turn.stopped)
        self.assertEqual(turn.tool_events, [])             # nothing dispatched
        self.assertIn("stop", turn.answer.lower())

    def test_stop_after_model_round_still_skips_the_tool(self):
        # stop flips True only AFTER the provider round returns its tool call — the dispatch guard
        # must still prevent the tool from firing.
        n = {"c": 0}
        def stop():
            n["c"] += 1
            return n["c"] >= 2           # False at the round-top check, True before dispatch
        script = [{"content": "", "tool_calls": [{"name": "system_health", "arguments": {}}]},
                  {"content": "done"}]
        provider = self.agent.router.mock(script)
        ctx = ToolContext(tenant_id="acme", actor="t")
        turn = self.agent.chat_stream(ctx, "go", lambda e: None,
                                      provider=provider, should_stop=stop)
        self.assertTrue(turn.stopped)
        self.assertEqual(turn.tool_events, [])
        self.assertEqual(turn.rounds, 1)

    def test_no_stop_completes_normally(self):
        provider = self.agent.router.mock([{"content": "all good"}])
        ctx = ToolContext(tenant_id="acme", actor="t")
        seen = []
        turn = self.agent.chat_stream(ctx, "go", lambda e: seen.append(e),
                                      provider=provider, should_stop=lambda: False)
        self.assertFalse(turn.stopped)
        self.assertEqual(turn.answer, "all good")
        self.assertTrue(any(e.get("type") == "delta" for e in seen))

    def test_defaults_to_manager_persona_with_signoff_rule(self):
        # No profile given → the agent runs as AtlasOps (default) and is told to sign as itself,
        # not as "MSP AI" (regression for the email signature issue).
        rec = _Recorder()
        ctx = ToolContext(tenant_id="acme", actor="tester")
        self.agent.chat(ctx, "hello", provider=rec)
        system = rec.seen[0]["content"]
        self.assertIn("AtlasOps Manager", system)
        self.assertIn('do not sign as "MSP AI"', system)

    def test_history_becomes_conversation_context(self):
        rec = _Recorder()
        ctx = ToolContext(tenant_id="acme", actor="tester")
        history = [
            {"role": "user", "content": "how many assets does acme have?"},
            {"role": "assistant", "content": "Acme has 42 assets."},
            {"role": "bogus", "content": "ignore me"},      # bad role dropped
            {"role": "assistant", "content": ""},             # empty dropped
        ]
        self.agent.chat(ctx, "which are offline?", provider=rec, history=history)
        roles = [m["role"] for m in rec.seen]
        self.assertEqual(rec.seen[0]["role"], "system")
        self.assertEqual(roles[1:], ["user", "assistant", "user"])  # 2 clean history + current
        self.assertEqual(rec.seen[-1]["content"], "which are offline?")
        self.assertNotIn("ignore me", [m["content"] for m in rec.seen])


    def test_summarize_uses_history_and_no_tools(self):
        rec = _Recorder()
        # summarize() resolves its own provider, so point the router at our recorder
        self.agent.router.resolve = lambda mid=None: (rec, "m")
        summary = self.agent.summarize([{"role": "user", "content": "how many assets?"},
                                        {"role": "assistant", "content": "42 assets."}])
        self.assertEqual(summary, "ok")
        self.assertEqual(rec.seen[0]["role"], "system")   # summarizer system prompt
        self.assertEqual(rec.seen[-1]["role"], "user")    # the flattened transcript
        self.assertIn("42 assets", rec.seen[-1]["content"])

    def test_summarize_empty_is_empty(self):
        self.assertEqual(self.agent.summarize([]), "")
        self.assertEqual(self.agent.summarize(None), "")


class HistoryGuard(unittest.TestCase):
    def test_drops_bad_entries_and_caps_count(self):
        h = [{"role": "user", "content": str(i)} for i in range(40)]
        h += [{"role": "tool", "content": "x"}, {"not": "a dict"}, 7]
        cleaned = clean_history(h, max_msgs=20, max_chars=10**9)
        self.assertEqual(len(cleaned), 20)                      # capped to max_msgs
        self.assertTrue(all(m["role"] in ("user", "assistant") for m in cleaned))
        self.assertEqual(cleaned[-1]["content"], "39")          # keeps the most recent

    def test_trims_oldest_past_char_budget(self):
        big = "x" * 5000
        cleaned = clean_history([
            {"role": "user", "content": big},
            {"role": "assistant", "content": big},
            {"role": "user", "content": "latest"},
        ], max_chars=6000)
        self.assertEqual(cleaned[-1]["content"], "latest")      # newest always survives
        self.assertLess(sum(len(m["content"]) for m in cleaned), 5000 * 3)

    def test_empty_is_safe(self):
        self.assertEqual(clean_history(None), [])
        self.assertEqual(clean_history([]), [])


class ProfileAware(unittest.TestCase):
    """Phase 2 — the loop can run AS a profile: its SOUL + memory shape the system prompt,
    layered BELOW the immutable safety contract."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        pp = d / "profiles" / "sentinelops"
        pp.mkdir(parents=True)
        (pp / "SOUL.md").write_text(
            "# SentinelOps\n## Identity\n- name: SentinelOps\n- role: Security Analyst\n")
        (pp / "MEMORY.md").write_text("# Memory\n- acme uses Huntress for EDR\n")
        (pp / "USER.md").write_text("the MSP — IT MSP.\n")
        env = d / ".env"
        env.write_text(f"MSPAI_ENV=dev\nMSPAI_AGENTS_DIR={d}\nMSPAI_VAULT_PATH={d}\n")
        env.chmod(0o600)
        self.cfg = Config(env_path=env)
        self.audit = AuditStore(d / "a.db")
        self.agent = Agent(Registry(), self.audit, ModelRouter(self.cfg),
                           gate=DenyAllApprovals(), cfg=self.cfg)

    def tearDown(self):
        self.audit.close()
        self.tmp.cleanup()

    def test_profile_persona_and_memory_in_system_prompt(self):
        rec = _Recorder()
        ctx = ToolContext(tenant_id="acme", actor="t")
        self.agent.chat(ctx, "status?", provider=rec, profile="sentinelops")
        sysmsg = rec.seen[0]["content"]
        self.assertIn("only through the tools", sysmsg.lower())   # safety base (tool fence) leads
        self.assertIn("approval", sysmsg.lower())                 # writes are approval-gated, not banned
        self.assertIn("SentinelOps", sysmsg)          # persona
        self.assertIn("Security Analyst", sysmsg)     # role from SOUL
        self.assertIn("Huntress", sysmsg)             # long-term memory injected
        self.assertIn("the MSP", sysmsg)       # USER.md (about the team)

    def _assert_base_only(self, sysmsg):
        """Base contract leads; the shared operating block may follow; NO persona/memory content."""
        self.assertTrue(sysmsg.startswith(SYSTEM_PROMPT))
        self.assertNotIn("Your persona", sysmsg)
        self.assertNotIn("long-term memory", sysmsg)

    def test_no_profile_is_base_plus_shared_block_only(self):
        rec = _Recorder()
        ctx = ToolContext(tenant_id="acme", actor="t")
        self.agent.chat(ctx, "hi", provider=rec)
        self._assert_base_only(rec.seen[0]["content"])

    def test_unknown_profile_falls_back_safely(self):
        rec = _Recorder()
        ctx = ToolContext(tenant_id="acme", actor="t")
        self.agent.chat(ctx, "hi", provider=rec, profile="ghost")
        self._assert_base_only(rec.seen[0]["content"])

    def test_profile_brain_drives_model_when_no_explicit_pick(self):
        """A profile's assigned brain must select the model on interactive runs — without this
        an agent silently lands on the local default no matter what brain you pick (the bug)."""
        from execution.core.agents import set_brain
        set_brain("sentinelops", "openai-codex:gpt-5.5", self.cfg)
        seen = {}
        def fake_resolve(mid=None):
            seen["mid"] = mid
            return _Recorder(), (mid or "").split(":", 1)[-1]
        self.agent.router.resolve = fake_resolve
        ctx = ToolContext(tenant_id="acme", actor="t")
        self.agent.chat(ctx, "hi", profile="sentinelops")          # no model_id
        self.assertEqual(seen["mid"], "openai-codex:gpt-5.5")      # brain chosen, not local

    def test_explicit_model_overrides_profile_brain(self):
        from execution.core.agents import set_brain
        set_brain("sentinelops", "openai-codex:gpt-5.5", self.cfg)
        seen = {}
        def fake_resolve(mid=None):
            seen["mid"] = mid
            return _Recorder(), (mid or "").split(":", 1)[-1]
        self.agent.router.resolve = fake_resolve
        ctx = ToolContext(tenant_id="acme", actor="t")
        self.agent.chat(ctx, "hi", profile="sentinelops", model_id="ollama:llama3.1")
        self.assertEqual(seen["mid"], "ollama:llama3.1")           # the dropdown pick wins

    def test_invalid_profile_name_does_not_crash(self):
        rec = _Recorder()                              # path-traversal name → fail safe, no raise
        ctx = ToolContext(tenant_id="acme", actor="t")
        self.agent.chat(ctx, "hi", provider=rec, profile="../etc")
        self._assert_base_only(rec.seen[0]["content"])

    def test_build_system_prompt_direct(self):
        self.assertTrue(build_system_prompt(None, self.cfg).startswith(SYSTEM_PROMPT))
        self.assertIn("SentinelOps", build_system_prompt("sentinelops", self.cfg))

    def test_client_memory_injected_for_bound_tenant(self):
        from execution.core.memory import VaultStore
        VaultStore(cfg=self.cfg).append_memory("acme", "Acme's firewall is a SonicWall TZ470", "tester")
        rec = _Recorder()
        ctx = ToolContext(tenant_id="acme", actor="t")
        self.agent.chat(ctx, "status?", provider=rec)          # no profile, bound to acme
        sysmsg = rec.seen[0]["content"]
        self.assertIn("SonicWall TZ470", sysmsg)               # recalled automatically — no tool call
        self.assertIn("acme", sysmsg)

    def test_client_memory_not_injected_for_star(self):
        from execution.core.memory import VaultStore
        VaultStore(cfg=self.cfg).append_memory("acme", "private note", "tester")
        rec = _Recorder()
        ctx = ToolContext(tenant_id="*", actor="t")
        self.agent.chat(ctx, "hi", provider=rec)               # '*' = cross-client → no single memory
        self._assert_base_only(rec.seen[0]["content"])
        self.assertNotIn("private note", rec.seen[0]["content"])


if __name__ == "__main__":
    unittest.main()
