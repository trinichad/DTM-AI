"""Agent loop test — drives a scripted mock model through a real tool call.

Proves the full Navigation path: model requests a tool -> dispatch runs it (guarded) ->
result re-enters context -> model returns a final, cited answer.
"""
import tempfile
import unittest
from pathlib import Path

from execution.agent import Agent, clean_history
from execution.core.audit import AuditStore
from execution.core.context import ToolContext
from execution.core.dispatch import DenyAllApprovals
from execution.core.registry import Registry
from execution.core.router import ChatResult, ModelRouter
from execution.core.config import Config


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
    p.write_text("DTM_ENV=dev\n", encoding="utf-8")
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


class HistoryGuard(unittest.TestCase):
    def test_drops_bad_entries_and_caps_count(self):
        h = [{"role": "user", "content": str(i)} for i in range(40)]
        h += [{"role": "tool", "content": "x"}, {"not": "a dict"}, 7]
        cleaned = clean_history(h)
        self.assertEqual(len(cleaned), 20)                      # capped to MAX_HISTORY_MSGS
        self.assertTrue(all(m["role"] in ("user", "assistant") for m in cleaned))
        self.assertEqual(cleaned[-1]["content"], "39")          # keeps the most recent

    def test_trims_oldest_past_char_budget(self):
        big = "x" * 5000
        cleaned = clean_history([
            {"role": "user", "content": big},
            {"role": "assistant", "content": big},
            {"role": "user", "content": "latest"},
        ])
        self.assertEqual(cleaned[-1]["content"], "latest")      # newest always survives
        self.assertLess(sum(len(m["content"]) for m in cleaned), 5000 * 3)

    def test_empty_is_safe(self):
        self.assertEqual(clean_history(None), [])
        self.assertEqual(clean_history([]), [])


if __name__ == "__main__":
    unittest.main()
