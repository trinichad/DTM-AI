"""Integration-skill tests — dispatch the real vendor skills against FAKE clients.

Proves the skill logic (slimming, args) and that it flows through dispatch's guardrails,
with no network and no credentials.
"""
import tempfile
import unittest
from pathlib import Path

from execution.core.audit import AuditStore
from execution.core.context import ToolContext
from execution.core.dispatch import dispatch
from execution.core.registry import Registry


class FakeKaseya:
    def get_assets(self):
        return [{"AgentId": 1, "AssetName": "PC1", "OSType": "Windows", "OSName": "Win11",
                 "IPAddresses": "10.0.0.5", "LastSeenDate": "2026-06-01", "extra": "drop me"}]

    def get_asset(self, asset_id):
        return {"AgentId": asset_id, "AssetName": "PC1", "LastLoggedInUser": "jdoe"}

    def get_agents(self):
        # agents present in the machine group but NOT necessarily as asset records
        return [{"AgentId": 11, "AgentName": "iwr-01.inwood.rho", "MachineGroup": "inwood.rho"},
                {"AgentId": 12, "AgentName": "iwr-02.inwood.rho", "MachineGroup": "inwood.rho"},
                {"AgentId": 99, "AgentName": "abc-01.other.rho", "MachineGroup": "other.rho"}]


class FakeCylance:
    def get_paginated(self, path, params=None, **kw):
        yield {"id": "1", "name": "d1", "state": "Online", "agent_version": "3.1", "secret": "x"}


class FakeHuntress:
    def get_paginated(self, path, params=None, **kw):
        yield {"id": 7, "hostname": "h1", "platform": "windows", "status": "sent", "junk": 1}


_CLIENTS = {"kaseya": FakeKaseya(), "cylance": FakeCylance(), "huntress": FakeHuntress()}


class IntegrationSkills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = AuditStore(Path(self.tmp.name) / "i.db")
        self.reg = Registry()  # production skills
        self.ctx = ToolContext(tenant_id="acme", actor="t",
                               client_factory=lambda integ, tenant: _CLIENTS[integ])

    def tearDown(self):
        self.audit.close()
        self.tmp.cleanup()

    def _d(self, name, args=None):
        return dispatch(registry=self.reg, audit=self.audit, ctx=self.ctx, name=name, args=args)

    def test_kaseya_list_assets_slims(self):
        env = self._d("kaseya_list_assets")
        self.assertTrue(env["ok"])
        self.assertEqual(env["source"], "kaseya")
        self.assertNotIn("extra", env["data"][0])      # payload slimmed
        self.assertEqual(env["data"][0]["AssetName"], "PC1")

    def test_kaseya_list_agents_and_filter(self):
        env = self._d("kaseya_list_agents")
        self.assertTrue(env["ok"])
        self.assertEqual(env["source"], "kaseya")
        self.assertEqual(len(env["data"]), 3)                       # all agents (machine-group view)
        # name_contains gives a complete focused result (the fix for the 'missing iwr-02' bug)
        env = self._d("kaseya_list_agents", {"name_contains": "inwood"})
        names = sorted(a["AgentName"] for a in env["data"])
        self.assertEqual(names, ["iwr-01.inwood.rho", "iwr-02.inwood.rho"])

    def test_kaseya_get_asset_requires_id(self):
        self.assertFalse(self._d("kaseya_get_asset", {})["ok"])         # missing required arg
        env = self._d("kaseya_get_asset", {"asset_id": "1"})
        self.assertTrue(env["ok"])
        self.assertEqual(env["data"]["LastLoggedInUser"], "jdoe")

    def test_cylance_devices(self):
        env = self._d("cylance_list_devices")
        self.assertTrue(env["ok"])
        self.assertNotIn("secret", env["data"][0])

    def test_cylance_devices_dedup_pagination_drift(self):
        # Regression for the bogus 1800 count (real 1708): Cylance pagination drifts and repeats
        # boundary records across pages. The skill must dedup by id, not count raw yields.
        class DriftingCylance:
            def get_paginated(self, path, params=None, **kw):
                for did in ["1", "2", "3", "2", "1"]:   # 5 yields, only 3 unique
                    yield {"id": did, "name": f"d{did}", "state": "Online"}
        ctx = ToolContext(tenant_id="acme", actor="t",
                          client_factory=lambda integ, tenant: DriftingCylance())
        env = dispatch(registry=self.reg, audit=self.audit, ctx=ctx, name="cylance_list_devices")
        self.assertTrue(env["ok"])
        self.assertEqual(len(env["data"]), 3)           # 1708-style dedup, not 1800-style raw count
        self.assertEqual([d["id"] for d in env["data"]], ["1", "2", "3"])  # first-seen order kept

    def test_huntress_incidents_enum_validation(self):
        self.assertFalse(self._d("huntress_list_incidents", {"status": "bogus"})["ok"])
        self.assertTrue(self._d("huntress_list_incidents", {"status": "sent"})["ok"])

    def test_missing_creds_is_clean_error(self):
        # a context with no client_factory -> tool fails closed, contained as an error envelope
        ctx = ToolContext(tenant_id="acme", actor="t")
        env = dispatch(registry=self.reg, audit=self.audit, ctx=ctx, name="kaseya_list_assets")
        self.assertFalse(env["ok"])


if __name__ == "__main__":
    unittest.main()
