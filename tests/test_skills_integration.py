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

    def test_cylance_devices_name_filter(self):
        # the name_contains filter is what makes cross-vendor joins possible on huge fleets
        class C:
            def get_paginated(self, path, params=None, **kw):
                for n in ["RHO-9SN4XD3", "ABC-01", "rho-lt32"]:
                    yield {"id": n, "name": n, "agent_version": "3.1"}
        ctx = ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: C())
        env = dispatch(registry=self.reg, audit=self.audit, ctx=ctx,
                       name="cylance_list_devices", args={"name_contains": "rho"})
        self.assertEqual(sorted(d["name"] for d in env["data"]), ["RHO-9SN4XD3", "rho-lt32"])

    def test_huntress_agents_name_filter(self):
        class H:
            def get_paginated(self, path, params=None, **kw):
                for h in ["RHO-9SN4XD3", "abc", "rho-lt32"]:
                    yield {"id": h, "hostname": h, "version": "0.14"}
        ctx = ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: H())
        env = dispatch(registry=self.reg, audit=self.audit, ctx=ctx,
                       name="huntress_list_agents", args={"name_contains": "rho"})
        self.assertEqual(sorted(a["hostname"] for a in env["data"]), ["RHO-9SN4XD3", "rho-lt32"])

    def test_endpoint_coverage_joins_three_vendors(self):
        class K:
            def get_agents(self):
                return [{"AgentName": "rho-01.root.rho", "ComputerName": "RHO-01", "Online": True},
                        {"AgentName": "rho-02.root.rho", "ComputerName": "RHO-02", "Online": False}]
        class C:
            def get_paginated(self, path, params=None, **kw):
                yield {"id": "1", "name": "RHO-01", "agent_version": "3.1.0"}   # only RHO-01 has Cylance
        class H:
            def get_paginated(self, path, params=None, **kw):
                yield {"id": "a", "hostname": "RHO-02", "version": "0.14.168"}  # only RHO-02 has Huntress
        clients = {"kaseya": K(), "cylance": C(), "huntress": H()}
        ctx = ToolContext(tenant_id="*", actor="t", client_factory=lambda i, t: clients[i])
        env = dispatch(registry=self.reg, audit=self.audit, ctx=ctx,
                       name="endpoint_coverage", args={"name_contains": "rho"})
        self.assertTrue(env["ok"])
        by = {r["hostname"]: r for r in env["data"]["machines"]}
        self.assertEqual(set(by), {"RHO-01", "RHO-02"})
        self.assertTrue(by["RHO-01"]["cylance"])
        self.assertEqual(by["RHO-01"]["cylance_version"], "3.1.0")
        self.assertFalse(by["RHO-01"]["huntress"])
        self.assertTrue(by["RHO-02"]["huntress"])
        self.assertEqual(by["RHO-02"]["huntress_version"], "0.14.168")
        self.assertTrue(by["RHO-01"]["kaseya_online"])
        self.assertFalse(by["RHO-02"]["kaseya_online"])
        self.assertEqual(env["data"]["summary"]["missing_huntress"], ["RHO-01"])
        self.assertEqual(env["data"]["summary"]["missing_cylance"], ["RHO-02"])

    def test_endpoint_coverage_requires_name(self):
        self.assertFalse(self._d("endpoint_coverage", {})["ok"])   # name_contains required by schema

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
