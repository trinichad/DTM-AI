"""Owner-approved connector self-extension (D-64) — floors, persistence, EXO merge, the tool."""
import os
import tempfile
import unittest
from pathlib import Path


class ConnectorGrantStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MSPAI_VAULT_PATH"] = self.tmp.name
        from execution.core import connector_grants
        self.cg = connector_grants

    def tearDown(self):
        os.environ.pop("MSPAI_VAULT_PATH", None)
        self.tmp.cleanup()

    def test_add_and_load_roundtrip(self):
        r = self.cg.add("exo", "Set-CASMailbox", "write", ["Identity", "OWAEnabled"],
                        reason="toggle OWA", by="admin")
        self.assertTrue(r["ok"], r)
        cmdlets, params = self.cg.grants_for("exo")
        self.assertEqual(cmdlets["Set-CASMailbox"], "write")
        self.assertEqual(params["Set-CASMailbox"], frozenset({"Identity", "OWAEnabled"}))
        # persisted to a 0600 json file
        p = Path(self.tmp.name) / "connector_grants.json"
        self.assertTrue(p.exists())
        self.assertEqual(oct(p.stat().st_mode)[-3:], "600")

    def test_floor_blocks_destructive_and_forbidden(self):
        self.assertFalse(self.cg.can_grant("exo", "Remove-Mailbox", "write")[0])     # destructive
        self.assertFalse(self.cg.can_grant("exo", "Remove-Mailbox", "destructive")[0])
        self.assertFalse(self.cg.can_grant("exo", "New-MailboxExportRequest", "write")[0])  # exfil
        self.assertFalse(self.cg.can_grant("exo", "Set-Mailbox", "write")[0])         # curated
        self.assertFalse(self.cg.can_grant("exo", "Get-Mailbox", "read")[0])          # built-in
        self.assertIn("destructive", self.cg.can_grant("exo", "X", "destructive")[1])
        self.assertFalse(self.cg.can_grant("graph", "Whatever", "read")[0])           # unknown
        # a clearly-new safe cmdlet passes
        self.assertTrue(self.cg.can_grant("exo", "Set-CASMailbox", "write")[0])

    def test_add_refuses_forbidden_even_if_called_directly(self):
        r = self.cg.add("exo", "Remove-Mailbox", "write", [], by="admin")
        self.assertFalse(r["ok"])
        self.assertEqual(self.cg.grants_for("exo"), ({}, {}))

    def test_grants_for_filters_a_now_forbidden_entry_on_disk(self):
        # defense in depth: even if a bad entry reached disk, it's never served
        import json
        p = Path(self.tmp.name) / "connector_grants.json"
        p.write_text(json.dumps({"exo": {"Remove-Mailbox": {"kind": "write", "params": []}}}))
        self.assertEqual(self.cg.grants_for("exo"), ({}, {}))

    def test_revoke(self):
        self.cg.add("exo", "Set-CASMailbox", "write", ["Identity"], by="admin")
        self.assertTrue(self.cg.revoke("exo", "Set-CASMailbox"))
        self.assertEqual(self.cg.grants_for("exo"), ({}, {}))
        self.assertFalse(self.cg.revoke("exo", "Nope"))


class EXOMergesGrants(unittest.TestCase):
    def _client(self, sink, granted, gparams):
        from execution.clients.exo import EXOClient
        return EXOClient(lambda: "TOK", "tid-1", "admin@x.com",
                         transport=lambda m, u, headers=None, json_body=None, **_:
                         sink.append(json_body) or (200, {"value": []}),
                         granted_cmdlets=granted, granted_params=gparams)

    def test_granted_cmdlet_runs_and_is_param_enforced(self):
        calls = []
        c = self._client(calls, {"Set-CASMailbox": "write"},
                         {"Set-CASMailbox": frozenset({"Identity", "OWAEnabled"})})
        # allowed params go through
        r = c.invoke("Set-CASMailbox", {"Identity": "a@x.com", "OWAEnabled": False})
        self.assertNotIn("error", r if isinstance(r, dict) else {})
        self.assertEqual(len(calls), 1)
        # a param outside the granted set is refused before HTTP
        r2 = c.invoke("Set-CASMailbox", {"Identity": "a@x.com", "ActiveSyncEnabled": False})
        self.assertIn("not in the allowlist", r2["error"])
        self.assertEqual(len(calls), 1)
        # a non-granted, non-builtin cmdlet is still refused
        self.assertIn("not in the EXO allowlist", c.invoke("Set-OtherThing", {})["error"])

    def test_a_destructive_grant_is_dropped_by_the_client(self):
        # even if grants_for somehow returned a destructive cmdlet, the client drops it
        calls = []
        c = self._client(calls, {"Remove-Mailbox": "write"}, {"Remove-Mailbox": frozenset()})
        self.assertIn("not in the EXO allowlist",
                      c.invoke("Remove-Mailbox", {"Identity": "x"})["error"])
        self.assertEqual(calls, [])


class ProposeCapabilityTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MSPAI_VAULT_PATH"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("MSPAI_VAULT_PATH", None)
        self.tmp.cleanup()

    def _ctx(self):
        from execution.core.context import ToolContext
        return ToolContext(tenant_id="acme", actor="admin")

    def test_tool_is_an_approval_gated_write(self):
        from execution.core.registry import Registry
        t = Registry().get("propose_connector_capability")
        self.assertIsNotNone(t)
        self.assertEqual(t.category, "write")
        self.assertTrue(t.requires_approval)
        self.assertFalse(t.enabled_by_default)

    def test_run_persists_a_grant_and_refuses_destructive(self):
        from execution.skills import propose_connector_capability as p
        from execution.core import connector_grants
        ok = p.run(self._ctx(), connector="exo", cmdlet="Set-CASMailbox", kind="write",
                   params=["Identity", "OWAEnabled"], reason="OWA toggle")
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(connector_grants.grants_for("exo")[0]["Set-CASMailbox"], "write")
        bad = p.run(self._ctx(), connector="exo", cmdlet="Remove-Mailbox", kind="write")
        self.assertFalse(bad["ok"])
        self.assertEqual(connector_grants.grants_for("exo")[0].get("Remove-Mailbox"), None)


if __name__ == "__main__":
    unittest.main()
