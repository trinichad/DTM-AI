"""Capability Console tests — prove the graduated-autonomy throttle is safe by default
and opens exactly as far as the owner sets, with the destructive safety floor intact.
"""
import tempfile
import unittest
from pathlib import Path

from execution.core.audit import AuditStore
from execution.core.capabilities import CapabilityStore
from execution.core.context import ToolContext
from execution.core.dispatch import dispatch
from execution.core.gates import ConfigurableApprovalGate
from execution.core.registry import Registry
from tests.fixture_skills import fx_write, fx_destructive


class CapabilityThrottle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "c.db"
        self.audit = AuditStore(db)
        self.caps = CapabilityStore(db)
        self.reg = Registry(package="tests.fixture_skills")
        self.gate = ConfigurableApprovalGate(self.caps, self.reg)
        self.ctx = ToolContext(tenant_id="acme", actor="tester")
        fx_write.EXECUTED["value"] = False
        fx_destructive.EXECUTED["value"] = False

    def tearDown(self):
        self.audit.close()
        self.caps.close()
        self.tmp.cleanup()

    def _d(self, name, token=None):
        return dispatch(registry=self.reg, audit=self.audit, ctx=self.ctx,
                        name=name, args={}, approval_token=token, gate=self.gate)

    # default: safe — writes blocked even though the tool is enabled
    def test_write_blocked_by_default(self):
        env = self._d("fx_write")
        self.assertFalse(env["ok"])
        self.assertFalse(fx_write.EXECUTED["value"])

    # owner opens write but keeps approval required -> needs a token
    def test_write_opened_still_needs_approval(self):
        self.caps.set("fx_write", allow_write=True, require_approval=True)
        self.assertFalse(self._d("fx_write")["ok"])             # no token
        self.assertTrue(self._d("fx_write", token="approved")["ok"])  # with token
        self.assertTrue(fx_write.EXECUTED["value"])

    # owner trusts the tool for autonomous use -> runs with no token
    def test_write_autonomous_when_trusted(self):
        self.caps.set("fx_write", allow_write=True, require_approval=False)
        env = self._d("fx_write")
        self.assertTrue(env["ok"])
        self.assertTrue(fx_write.EXECUTED["value"])

    # SAFETY FLOOR: destructive ALWAYS needs a token, even if owner set require_approval False
    def test_destructive_floor_holds(self):
        self.caps.set("fx_destructive", allow_write=True, require_approval=False)
        self.assertFalse(self._d("fx_destructive")["ok"])        # floor forces approval
        self.assertFalse(fx_destructive.EXECUTED["value"])
        self.assertTrue(self._d("fx_destructive", token="approved")["ok"])
        self.assertTrue(fx_destructive.EXECUTED["value"])

    # policy persists and round-trips
    def test_policy_persists(self):
        self.caps.set("fx_write", allow_write=True, require_approval=False)
        p = self.caps.get("fx_write", default_enabled=True)
        self.assertTrue(p.allow_write)
        self.assertFalse(p.require_approval)


if __name__ == "__main__":
    unittest.main()
