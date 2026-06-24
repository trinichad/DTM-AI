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


class AutoApproveGate:
    """Trusted-write gate: write flag on, every write auto-approves (require_approval=False)."""
    def write_allowed_for_tenant(self, tenant_id, tool):
        return True
    def needs_approval(self, tenant_id, tool):
        return False
    def consume(self, token, tenant_id, tool, args):
        return True


class PendingGate:
    """Write needs human approval and never auto-runs (production ConfigurableApprovalGate shape)."""
    def write_allowed_for_tenant(self, tenant_id, tool):
        return True
    def needs_approval(self, tenant_id, tool):
        return True
    def consume(self, token, tenant_id, tool, args):
        return False


class FakeApprovals:
    def __init__(self):
        self.created = []
    def create(self, **kw):
        self.created.append(kw)
        return 100 + len(self.created)


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

    # ── bulk meta-tool (D-111) ────────────────────────────────────────────────────────────────
    def test_bulk_read_fans_out_in_one_call(self):
        env = self._dispatch("bulk", {"tool": "fx_read",
                                      "items": [{"x": "a"}, {"x": "b"}, {"x": "c"}]})
        self.assertTrue(env["ok"])
        self.assertEqual(env["data"]["count"], 3)
        self.assertEqual(env["data"]["ok_count"], 3)
        self.assertEqual([r["data"]["echo"] for r in env["data"]["results"]], ["a", "b", "c"])

    def test_bulk_validates_each_item_independently(self):
        # a bad item (missing required arg) fails just that item; the rest still run
        env = self._dispatch("bulk", {"tool": "fx_read",
                                      "items": [{"x": "ok"}, {"nope": 1}]})
        self.assertTrue(env["ok"])
        self.assertEqual(env["data"]["ok_count"], 1)
        self.assertEqual(env["data"]["error_count"], 1)
        self.assertFalse(env["data"]["results"][1]["ok"])

    def test_bulk_enforces_write_gate_per_item(self):
        # bulk grants NO authority: a write with no flag is blocked for every item, none execute
        env = self._dispatch("bulk", {"tool": "fx_write", "items": [{}, {}]},
                             gate=DenyAllApprovals())
        self.assertTrue(env["ok"])                      # the bulk call itself completes
        self.assertEqual(env["data"]["ok_count"], 0)    # but every write item was blocked
        self.assertFalse(fx_write.EXECUTED["value"], "no write may run without the flag")

    def test_bulk_runs_trusted_writes_in_one_call(self):
        env = self._dispatch("bulk", {"tool": "fx_write", "items": [{}, {}, {}]},
                             gate=AutoApproveGate())
        self.assertTrue(env["ok"])
        self.assertEqual(env["data"]["ok_count"], 3)
        self.assertTrue(fx_write.EXECUTED["value"])

    def test_bulk_pauses_on_first_approval_needed(self):
        # an item needing human sign-off surfaces ONE approval card and stops (no orphan pile-up)
        approvals = FakeApprovals()
        env = self._dispatch("bulk", {"tool": "fx_write", "items": [{}, {}, {}]},
                             gate=PendingGate(), approvals=approvals)
        self.assertEqual(env.get("status"), "pending_approval")
        self.assertEqual(len(approvals.created), 1)     # exactly one card, not three
        self.assertEqual(env["bulk"]["remaining"], 3)
        self.assertEqual(env["bulk"]["paused_index"], 0)

    def test_bulk_rejects_nesting_and_unknown_inner(self):
        self.assertIn("no nesting",
                      self._dispatch("bulk", {"tool": "bulk", "items": []})["error"])
        self.assertIn("unknown tool",
                      self._dispatch("bulk", {"tool": "ghost", "items": [{}]})["error"])

    def test_bulk_item_cap(self):
        env = self._dispatch("bulk", {"tool": "fx_read",
                                      "items": [{"x": str(i)} for i in range(201)]})
        self.assertFalse(env["ok"])
        self.assertIn("exceeds", env["error"])


if __name__ == "__main__":
    unittest.main()
