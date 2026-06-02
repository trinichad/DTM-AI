"""Write-action approval workflow — propose -> human approve -> execute (args-bound, one-shot)."""
import os
import tempfile
import unittest
from pathlib import Path

from execution.core.context import ToolContext
from execution.core.dispatch import dispatch
from execution.core.memory import VaultStore
from execution.runtime import build_agent
from execution.web.api import Api
from execution.web.auth import AuthStore, SessionSigner


class ApprovalFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "a.db"
        os.environ["DTM_VAULT_PATH"] = str(Path(self.tmp.name) / "vault")
        self.agent = build_agent(db_path=self.db)
        # make memory_note (an internal write) REQUIRE approval, to exercise the workflow
        self.agent.caps.set("memory_note", allow_write=True, require_approval=True)
        self.auth = AuthStore(self.db)
        self.auth.ensure_admin("adminpass")
        self.auth.create_user("tech1", "techpass1", "user")
        self.api = Api(self.agent, self.auth, SessionSigner(secret=b"0" * 32))
        self.ctx = ToolContext(tenant_id="acme", actor="hermes")

    def tearDown(self):
        os.environ.pop("DTM_VAULT_PATH", None)
        self.auth.close()
        self.tmp.cleanup()

    def _write(self, note="Sunday maintenance"):
        return dispatch(registry=self.agent.registry, audit=self.agent.audit, ctx=self.ctx,
                        name="memory_note", args={"note": note},
                        gate=self.agent.gate, approvals=self.agent.approvals)

    def test_write_creates_pending_and_does_not_execute(self):
        env = self._write()
        self.assertFalse(env["ok"])
        self.assertEqual(env["status"], "pending_approval")
        self.assertIsNotNone(env["approval_id"])
        # nothing written to the vault yet
        self.assertEqual(VaultStore().read_memory("acme"), "")
        self.assertEqual(self.agent.approvals.count_pending(), 1)

    def test_approve_executes_with_exact_args(self):
        aid = self._write("VPN renewal in August")["approval_id"]
        r = self.api.handle("POST", f"/api/approvals/{aid}/approve", {}, {}, "admin")
        self.assertEqual(r.status, 200)
        self.assertTrue(r.payload["executed"])
        # the exact proposed note is now in the vault
        self.assertIn("VPN renewal in August", VaultStore().read_memory("acme"))
        self.assertEqual(self.agent.approvals.get(aid)["status"], "executed")

    def test_reject_does_not_execute(self):
        aid = self._write("should not happen")["approval_id"]
        r = self.api.handle("POST", f"/api/approvals/{aid}/reject", {}, {}, "admin")
        self.assertEqual(r.status, 200)
        self.assertEqual(VaultStore().read_memory("acme"), "")
        self.assertEqual(self.agent.approvals.get(aid)["status"], "rejected")

    def test_one_shot(self):
        aid = self._write()["approval_id"]
        self.assertEqual(self.api.handle("POST", f"/api/approvals/{aid}/approve", {}, {}, "admin").status, 200)
        # second approve must fail (already decided)
        self.assertEqual(self.api.handle("POST", f"/api/approvals/{aid}/approve", {}, {}, "admin").status, 409)

    def test_non_admin_cannot_approve(self):
        aid = self._write()["approval_id"]
        self.assertEqual(self.api.handle("POST", f"/api/approvals/{aid}/approve", {}, {}, "tech1").status, 403)
        self.assertEqual(self.agent.approvals.get(aid)["status"], "pending")  # untouched

    def test_trusted_write_skips_approval(self):
        # with require_approval False, the same internal write runs inline (no pending)
        self.agent.caps.set("memory_note", allow_write=True, require_approval=False)
        env = self._write("trusted note")
        self.assertTrue(env["ok"])
        self.assertEqual(self.agent.approvals.count_pending(), 0)
        self.assertIn("trusted note", VaultStore().read_memory("acme"))


if __name__ == "__main__":
    unittest.main()
