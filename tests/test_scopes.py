"""Scoped-connector tests — prove the read allowlist is a real boundary (D-15).

This is the safety proof for the 'learned skills compose only guarded primitives' model:
the AI can read allow-listed paths but CANNOT reach write/auth endpoints, other hosts, or
escape via traversal — and a blocked path never touches the vendor client.
"""
import tempfile
import unittest
from pathlib import Path

from execution.core.audit import AuditStore
from execution.core.context import ToolContext
from execution.core.dispatch import dispatch
from execution.core.registry import Registry
from execution.clients.scopes import is_allowed_read


class Allowlist(unittest.TestCase):
    def test_allowed_paths(self):
        self.assertTrue(is_allowed_read("kaseya", "/assetmgmt/assets")[0])
        self.assertTrue(is_allowed_read("cylance", "/devices/v2")[0])
        self.assertTrue(is_allowed_read("huntress", "/incident_reports")[0])

    def test_blocks_non_allowlisted(self):
        self.assertFalse(is_allowed_read("kaseya", "/system/scripts/run")[0])
        self.assertFalse(is_allowed_read("huntress", "/settings")[0])  # not a read prefix

    def test_boundary_match_not_loose_prefix(self):
        # "/account" must not match "/account_settings"
        self.assertTrue(is_allowed_read("huntress", "/account")[0])
        self.assertTrue(is_allowed_read("huntress", "/account/usage")[0])
        self.assertFalse(is_allowed_read("huntress", "/account_settings")[0])

    def test_blocks_auth_endpoints(self):
        self.assertFalse(is_allowed_read("cylance", "/auth/v2/token")[0])
        self.assertFalse(is_allowed_read("kaseya", "/auth")[0])

    def test_blocks_host_escape(self):
        self.assertFalse(is_allowed_read("kaseya", "https://evil.com/x")[0])
        self.assertFalse(is_allowed_read("kaseya", "//evil.com/x")[0])
        self.assertFalse(is_allowed_read("kaseya", "/assetmgmt/../../auth")[0])
        self.assertFalse(is_allowed_read("kaseya", "no-leading-slash")[0])

    def test_unknown_vendor(self):
        self.assertFalse(is_allowed_read("acme", "/x")[0])


class RecordingClient:
    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        return {"ok": True, "path": path}


class ConnectorViaDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = AuditStore(Path(self.tmp.name) / "s.db")
        self.reg = Registry()
        self.client = RecordingClient()
        self.ctx = ToolContext(tenant_id="acme", actor="hermes",
                               client_factory=lambda integ, tenant: self.client)

    def tearDown(self):
        self.audit.close()
        self.tmp.cleanup()

    def _d(self, name, args):
        return dispatch(registry=self.reg, audit=self.audit, ctx=self.ctx, name=name, args=args)

    def test_allowed_read_calls_client(self):
        env = self._d("kaseya_read", {"path": "/assetmgmt/agents"})
        self.assertTrue(env["ok"])
        self.assertEqual(self.client.calls, [("/assetmgmt/agents", None)])

    def test_blocked_read_never_calls_client(self):
        env = self._d("cylance_read", {"path": "/auth/v2/token"})
        self.assertFalse(env["ok"])
        self.assertIn("read blocked", env["error"])
        self.assertEqual(self.client.calls, [], "client must NOT be called for a blocked path")

    def test_traversal_blocked(self):
        env = self._d("kaseya_read", {"path": "/assetmgmt/../../auth"})
        self.assertFalse(env["ok"])
        self.assertEqual(self.client.calls, [])


if __name__ == "__main__":
    unittest.main()
