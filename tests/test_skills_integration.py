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
        return [{"AgentId": 11, "AgentName": "iwr-01.acme.local", "MachineGroup": "acme.local"},
                {"AgentId": 12, "AgentName": "iwr-02.acme.local", "MachineGroup": "acme.local"},
                {"AgentId": 99, "AgentName": "abc-01.other.local", "MachineGroup": "other.local"}]


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
        env = self._d("kaseya_list_agents", {"name_contains": "acme"})
        names = sorted(a["AgentName"] for a in env["data"])
        self.assertEqual(names, ["iwr-01.acme.local", "iwr-02.acme.local"])

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
                for n in ["ACME-1234", "ABC-01", "acme-lt32"]:
                    yield {"id": n, "name": n, "agent_version": "3.1"}
        ctx = ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: C())
        env = dispatch(registry=self.reg, audit=self.audit, ctx=ctx,
                       name="cylance_list_devices", args={"name_contains": "acme"})
        self.assertEqual(sorted(d["name"] for d in env["data"]), ["ACME-1234", "acme-lt32"])

    def test_huntress_agents_name_filter(self):
        class H:
            def get_paginated(self, path, params=None, **kw):
                for h in ["ACME-1234", "abc", "acme-lt32"]:
                    yield {"id": h, "hostname": h, "version": "0.14"}
        ctx = ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: H())
        env = dispatch(registry=self.reg, audit=self.audit, ctx=ctx,
                       name="huntress_list_agents", args={"name_contains": "acme"})
        self.assertEqual(sorted(a["hostname"] for a in env["data"]), ["ACME-1234", "acme-lt32"])

    def test_endpoint_coverage_joins_three_vendors(self):
        class K:
            def get_agents(self):
                return [{"AgentName": "acme-01.root.local", "ComputerName": "ACME-01", "Online": True},
                        {"AgentName": "acme-02.root.local", "ComputerName": "ACME-02", "Online": False}]
        class C:
            def get_paginated(self, path, params=None, **kw):
                yield {"id": "1", "name": "ACME-01", "agent_version": "3.1.0"}   # only ACME-01 has Cylance
        class H:
            def get_paginated(self, path, params=None, **kw):
                yield {"id": "a", "hostname": "ACME-02", "version": "0.14.168"}  # only ACME-02 has Huntress
        clients = {"kaseya": K(), "cylance": C(), "huntress": H()}
        ctx = ToolContext(tenant_id="*", actor="t", client_factory=lambda i, t: clients[i])
        env = dispatch(registry=self.reg, audit=self.audit, ctx=ctx,
                       name="endpoint_coverage", args={"name_contains": "acme"})
        self.assertTrue(env["ok"])
        by = {r["hostname"]: r for r in env["data"]["machines"]}
        self.assertEqual(set(by), {"ACME-01", "ACME-02"})
        self.assertTrue(by["ACME-01"]["cylance"])
        self.assertEqual(by["ACME-01"]["cylance_version"], "3.1.0")
        self.assertFalse(by["ACME-01"]["huntress"])
        self.assertTrue(by["ACME-02"]["huntress"])
        self.assertEqual(by["ACME-02"]["huntress_version"], "0.14.168")
        self.assertTrue(by["ACME-01"]["kaseya_online"])
        self.assertFalse(by["ACME-02"]["kaseya_online"])
        self.assertEqual(env["data"]["summary"]["missing_huntress"], ["ACME-01"])
        self.assertEqual(env["data"]["summary"]["missing_cylance"], ["ACME-02"])

    def test_endpoint_coverage_group_scope_matches_edr_by_hostname(self):
        # REGRESSION (the Hilltop incident): scoping by a GROUP/site token ("acme") that appears in
        # the Kaseya machine group but NOT in the bare hostnames must still join Cylance/Huntress —
        # otherwise it falsely reports "no security coverage". Hosts are HILLTOP-*; group is
        # site1.acme; the EDR/MDR devices only know the bare hostname.
        class K:
            def get_agents(self):
                return [{"AgentName": "HILLTOP-LT02.site1.acme", "ComputerName": "HILLTOP-LT02",
                         "MachineGroup": "site1.acme", "Online": True},
                        {"AgentName": "HILLTOP-LT03.site1.acme", "ComputerName": "HILLTOP-LT03",
                         "MachineGroup": "site1.acme", "Online": True}]
        class C:
            def get_paginated(self, path, params=None, **kw):
                yield {"id": "1", "name": "HILLTOP-LT02", "agent_version": "3.4.1000"}
                yield {"id": "2", "name": "HILLTOP-LT03", "agent_version": "3.4.1000"}
        class H:
            def get_paginated(self, path, params=None, **kw):
                yield {"id": "a", "hostname": "HILLTOP-LT02", "version": "0.14.168"}
                yield {"id": "b", "hostname": "HILLTOP-LT03", "version": "0.14.168"}
        clients = {"kaseya": K(), "cylance": C(), "huntress": H()}
        ctx = ToolContext(tenant_id="*", actor="t", client_factory=lambda i, t: clients[i])
        env = dispatch(registry=self.reg, audit=self.audit, ctx=ctx,
                       name="endpoint_coverage", args={"name_contains": "acme"})
        self.assertTrue(env["ok"])
        s = env["data"]["summary"]
        self.assertEqual(s["with_cylance"], 2)         # was 0 before the fix (the false gap)
        self.assertEqual(s["with_huntress"], 2)
        self.assertEqual(s["missing_cylance"], [])
        self.assertEqual(s["missing_huntress"], [])

    def test_endpoint_coverage_real_gap_still_detected(self):
        # The fix must NOT mask genuine gaps: a Kaseya machine truly absent from Cylance/Huntress
        # still shows as missing.
        class K:
            def get_agents(self):
                return [{"ComputerName": "HILLTOP-LT02", "MachineGroup": "site1.acme", "Online": True},
                        {"ComputerName": "HILLTOP2-BUS02", "MachineGroup": "site1.acme", "Online": False}]
        class C:
            def get_paginated(self, path, params=None, **kw):
                yield {"id": "1", "name": "HILLTOP-LT02", "agent_version": "3.4.1000"}
        class H:
            def get_paginated(self, path, params=None, **kw):
                yield {"id": "a", "hostname": "HILLTOP-LT02", "version": "0.14.168"}
        clients = {"kaseya": K(), "cylance": C(), "huntress": H()}
        ctx = ToolContext(tenant_id="*", actor="t", client_factory=lambda i, t: clients[i])
        env = dispatch(registry=self.reg, audit=self.audit, ctx=ctx,
                       name="endpoint_coverage", args={"name_contains": "acme"})
        s = env["data"]["summary"]
        self.assertEqual(s["with_cylance"], 1)
        self.assertEqual(s["missing_cylance"], ["HILLTOP2-BUS02"])
        self.assertEqual(s["missing_huntress"], ["HILLTOP2-BUS02"])

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
