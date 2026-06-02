"""Agent loop test — drives a scripted mock model through a real tool call.

Proves the full Navigation path: model requests a tool -> dispatch runs it (guarded) ->
result re-enters context -> model returns a final, cited answer.
"""
import tempfile
import unittest
from pathlib import Path

from execution.agent import Agent
from execution.core.audit import AuditStore
from execution.core.context import ToolContext
from execution.core.dispatch import DenyAllApprovals
from execution.core.registry import Registry
from execution.core.router import ModelRouter
from execution.core.config import Config


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


if __name__ == "__main__":
    unittest.main()
