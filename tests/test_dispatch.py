"""Guardrail tests — prove dispatch() enforces the constitution's Behavioral Rules.

These are the tests that matter most: they verify that the security model is ENFORCED
in code, not merely documented.
"""
import tempfile
import unittest
from pathlib import Path

from execution.core.audit import AuditStore
from execution.core.context import ToolContext
from execution.core.dispatch import DenyAllApprovals, dispatch
from execution.core.registry import Registry
from tests.fixture_skills import fx_write


class AllowGate:
    """Test gate: tenant has write flag; approval token must equal 'good'."""
    def write_allowed_for_tenant(self, tenant_id, tool):
        return True
    def consume(self, token, tenant_id, tool, args):
        return token == "good"


class DispatchGuardrails(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = AuditStore(Path(self.tmp.name) / "t.db")
        self.reg = Registry(package="tests.fixture_skills")
        self.ctx = ToolContext(tenant_id="acme", actor="tester")
        fx_write.EXECUTED["value"] = False

    def tearDown(self):
        self.audit.close()
        self.tmp.cleanup()

    def _dispatch(self, name, args=None, **kw):
        return dispatch(registry=self.reg, audit=self.audit, ctx=self.ctx,
                        name=name, args=args, **kw)

    # 1. happy path: a read tool with valid args runs
    def test_read_tool_runs(self):
        env = self._dispatch("fx_read", {"x": "hi"})
        self.assertTrue(env["ok"])
        self.assertEqual(env["data"], {"echo": "hi", "tenant": "acme"})
        self.assertEqual(env["source"], "fixture")

    # 2. invalid args are rejected BEFORE run()
    def test_missing_required_arg_denied(self):
        env = self._dispatch("fx_read", {})
        self.assertFalse(env["ok"])
        self.assertIn("missing required", env["error"])

    def test_unknown_arg_denied(self):
        env = self._dispatch("fx_read", {"x": "hi", "evil": 1})
        self.assertFalse(env["ok"])
        self.assertIn("unexpected argument", env["error"])

    # 3. kill switch: disabled tool refuses even when named
    def test_disabled_tool_denied(self):
        self.audit.set_enabled("fx_read", False)
        env = self._dispatch("fx_read", {"x": "hi"})
        self.assertFalse(env["ok"])
        self.assertIn("disabled", env["error"])

    # 4. unknown tool
    def test_unknown_tool_denied(self):
        env = self._dispatch("does_not_exist")
        self.assertFalse(env["ok"])
        self.assertIn("unknown tool", env["error"])

    # 5. write tool is BLOCKED by default (read-only platform) and never executes
    def test_write_blocked_by_default(self):
        env = self._dispatch("fx_write", {}, gate=DenyAllApprovals())
        self.assertFalse(env["ok"])
        self.assertIn("no write flag", env["error"])
        self.assertFalse(fx_write.EXECUTED["value"], "write tool must NOT have run")

    # 6. write tool with flag but no/invalid approval token is blocked
    def test_write_blocked_without_token(self):
        env = self._dispatch("fx_write", {}, gate=AllowGate(), approval_token=None)
        self.assertFalse(env["ok"])
        self.assertIn("approval", env["error"])
        self.assertFalse(fx_write.EXECUTED["value"])

    # 7. write tool with flag + valid one-shot token runs
    def test_write_allowed_with_token(self):
        env = self._dispatch("fx_write", {}, gate=AllowGate(), approval_token="good")
        self.assertTrue(env["ok"])
        self.assertTrue(fx_write.EXECUTED["value"])

    # 8. tenant isolation: a tool cannot act on another tenant
    def test_cross_tenant_blocked(self):
        env = self._dispatch("fx_crosstenant", {"target": "other-client"})
        self.assertFalse(env["ok"])
        self.assertIn("tenant isolation", env["error"])

    def test_same_tenant_ok(self):
        env = self._dispatch("fx_crosstenant", {"target": "acme"})
        self.assertTrue(env["ok"])

    # 9. a raising tool returns an error envelope, never crashes the loop
    def test_raising_tool_is_contained(self):
        env = self._dispatch("fx_boom")
        self.assertFalse(env["ok"])
        self.assertIn("kaboom", env["error"])

    # 10. every call is audited
    def test_calls_are_audited(self):
        self._dispatch("fx_read", {"x": "hi"})
        self._dispatch("does_not_exist")
        rows = self.audit.query(tenant_id="acme", limit=50)
        actions = {r["action"] for r in rows}
        self.assertIn("tool_call", actions)
        self.assertIn("tool_denied", actions)
        # args are stored hashed, never raw
        for r in rows:
            self.assertNotIn("hi", str(r.get("args_hash") or ""))


if __name__ == "__main__":
    unittest.main()
