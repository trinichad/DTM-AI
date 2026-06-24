"""Native bulk/list params on Cylance + UniFi per-device skills — act on many in ONE call.

One representative selection: a couple Cylance reads/writes + a couple UniFi tools. Each batch
case passes the list param for 2 ids and checks: results length 2, devices_done==2, per-row
attribution, and that the single (non-list) path still behaves as before.
"""
import unittest

from execution.core.context import ToolContext


class FakeEDR:
    """Records get()/write()/write_destructive(); returns canned data. get_paginated yields rows."""
    def __init__(self, get_data=None, rows=None):
        self.get_data = get_data if get_data is not None else {}
        self.rows = rows or []
        self.gets = []
        self.writes = []

    def get(self, path, params=None):
        self.gets.append((path, params))
        return self.get_data

    def get_paginated(self, path, params=None, **_):
        self.gets.append((path, params))
        yield from self.rows

    def write(self, method, path, body=None):
        self.writes.append((method, path, body))
        return {"ok": True}

    def write_destructive(self, method, path, body=None):
        self.writes.append((method, path, body))
        return {"ok": True}


class FakeUnifi:
    def __init__(self, sites=None, rows=None):
        self._sites = sites if sites is not None else [{"id": "site-1", "name": "Default"}]
        self.rows = rows or []
        self.gets, self.writes = [], []

    def get(self, path, params=None):
        self.gets.append((path, params))
        if path == "/v1/sites":
            return {"data": self._sites}
        return {"data": self.rows}

    def get_paginated(self, path, params=None, **_):
        self.gets.append((path, params))
        yield from self.rows

    def write(self, method, path, body=None):
        self.writes.append((method, path, body))
        return {"ok": True}

    def write_destructive(self, method, path, body=None):
        self.writes.append((method, path, body))
        return {"ok": True}


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda integ, tenant: fake)


def _assert_batch(tc, out, n=2):
    tc.assertEqual(out["devices_done"], n)
    tc.assertEqual(len(out["results"]), n)
    return out["results"]


class CylanceNative(unittest.TestCase):
    def test_device_detail_batch_and_single(self):
        from execution.skills import cylance_device_detail as dd
        fake = FakeEDR(get_data={"name": "PC", "ok": True})
        out = dd.run(_ctx(fake), device_ids=["dev-1", "dev-2"])
        _assert_batch(self, out)
        self.assertEqual([g[0] for g in fake.gets],
                         ["/devices/v2/dev-1", "/devices/v2/dev-2"])
        self.assertEqual(out["ok_count"], 2)
        # single path unchanged — returns the raw client.get() record
        single = dd.run(_ctx(FakeEDR(get_data={"name": "X"})), device_id="dev-9")
        self.assertEqual(single, {"name": "X"})

    def test_device_threats_batch_attribution(self):
        from execution.skills import cylance_device_threats as dt
        fake = FakeEDR(rows=[{"name": "evil", "sha256": "a" * 64}])
        out = dt.run(_ctx(fake), device_ids=["dev-1", "dev-2"])
        rows = _assert_batch(self, out)
        self.assertEqual([r["device_id"] for r in rows], ["dev-1", "dev-2"])
        self.assertTrue(all(r["ok"] for r in rows))
        self.assertEqual(rows[0]["threats"][0]["name"], "evil")
        # single path unchanged — bare list of threats
        single = dt.run(_ctx(FakeEDR(rows=[{"name": "evil"}])), device_id="dev-9")
        self.assertIsInstance(single, list)
        self.assertEqual(single[0]["name"], "evil")

    def test_update_threat_batch_one_action_many_devices(self):
        from execution.skills import cylance_update_threat as ut
        fake = FakeEDR()
        out = ut.run(_ctx(fake), device_ids=["dev-1", "dev-2"], sha256="a" * 64, action="waive")
        rows = _assert_batch(self, out)
        self.assertEqual([r["device_id"] for r in rows], ["dev-1", "dev-2"])
        self.assertEqual(out["ok_count"], 2)
        self.assertEqual(fake.writes[0], ("PUT", "/devices/v2/dev-1/threats",
                                          {"threat_id": "a" * 64, "event": "Waive"}))
        self.assertEqual(fake.writes[1][1], "/devices/v2/dev-2/threats")
        # single path unchanged
        s = ut.run(_ctx(FakeEDR()), device_id="dev-9", sha256="a" * 64, action="quarantine")
        self.assertTrue(s["ok"])
        self.assertEqual(s["action"], "Quarantine")

    def test_assign_policy_batch_one_policy_many_devices(self):
        from execution.skills import cylance_assign_policy as ap
        fake = FakeEDR(get_data={"name": "PC", "id": "x"})
        out = ap.run(_ctx(fake), device_ids=["dev-1", "dev-2"], policy_id="pol-9")
        rows = _assert_batch(self, out)
        self.assertEqual([r["device_id"] for r in rows], ["dev-1", "dev-2"])
        self.assertTrue(all(r["policy_id"] == "pol-9" for r in rows))
        self.assertEqual(out["ok_count"], 2)
        # single path unchanged — reads name then PUTs
        sfake = FakeEDR(get_data={"name": "PC-1"})
        s = ap.run(_ctx(sfake), device_id="dev-9", policy_id="pol-1")
        self.assertTrue(s["ok"])
        self.assertEqual(sfake.writes[0], ("PUT", "/devices/v2/dev-9",
                                           {"name": "PC-1", "policy_id": "pol-1"}))

    def test_delete_device_batch_destructive(self):
        from execution.skills import cylance_delete_device as dl
        fake = FakeEDR()
        out = dl.run(_ctx(fake), device_ids=["dev-1", "dev-2"])
        rows = _assert_batch(self, out)
        self.assertEqual([r["device_id"] for r in rows], ["dev-1", "dev-2"])
        self.assertEqual(fake.writes[0], ("DELETE", "/devices/v2", {"device_ids": ["dev-1"]}))
        self.assertEqual(fake.writes[1], ("DELETE", "/devices/v2", {"device_ids": ["dev-2"]}))
        # single path unchanged
        sfake = FakeEDR()
        s = dl.run(_ctx(sfake), device_id="dev-9")
        self.assertTrue(s["ok"])
        self.assertEqual(sfake.writes[0], ("DELETE", "/devices/v2", {"device_ids": ["dev-9"]}))


class UnifiNative(unittest.TestCase):
    def test_device_detail_batch_resolves_site_each(self):
        from execution.skills import unifi_device_detail as dd
        fake = FakeUnifi(rows=[])
        out = dd.run(_ctx(fake), device_ids=["d-1", "d-2"])
        _assert_batch(self, out)
        dev_gets = [g[0] for g in fake.gets if g[0] != "/v1/sites"]
        self.assertEqual(dev_gets, ["/v1/sites/site-1/devices/d-1",
                                    "/v1/sites/site-1/devices/d-2"])
        # single path unchanged — returns raw client.get() of one device
        sfake = FakeUnifi()
        dd.run(_ctx(sfake), device_id="d-9")
        self.assertEqual(sfake.gets[-1][0], "/v1/sites/site-1/devices/d-9")

    def test_restart_batch_attribution(self):
        from execution.skills import unifi_restart_device as rd
        fake = FakeUnifi()
        out = rd.run(_ctx(fake), device_ids=["d-1", "d-2"])
        rows = _assert_batch(self, out)
        self.assertEqual([r["device_id"] for r in rows], ["d-1", "d-2"])
        self.assertEqual(out["ok_count"], 2)
        self.assertEqual(fake.writes[0], ("POST", "/v1/sites/site-1/devices/d-1/actions",
                                          {"action": "RESTART"}))
        self.assertEqual(fake.writes[1][1], "/v1/sites/site-1/devices/d-2/actions")
        # single path unchanged
        sfake = FakeUnifi()
        s = rd.run(_ctx(sfake), device_id="d-9")
        self.assertTrue(s["ok"])
        self.assertEqual(sfake.writes[0][1], "/v1/sites/site-1/devices/d-9/actions")

    def test_port_cycle_batch_same_port_many_switches(self):
        from execution.skills import unifi_port_cycle as pc
        fake = FakeUnifi()
        out = pc.run(_ctx(fake), device_ids=["sw-1", "sw-2"], port=7)
        _assert_batch(self, out)
        self.assertEqual(fake.writes[0][1],
                         "/v1/sites/site-1/devices/sw-1/interfaces/ports/7/actions")
        self.assertEqual(fake.writes[1][1],
                         "/v1/sites/site-1/devices/sw-2/interfaces/ports/7/actions")
        # single path unchanged
        sfake = FakeUnifi()
        pc.run(_ctx(sfake), device_id="sw-9", port=3)
        self.assertEqual(sfake.writes[0][1],
                         "/v1/sites/site-1/devices/sw-9/interfaces/ports/3/actions")

    def test_client_action_batch_same_action_many_clients(self):
        from execution.skills import unifi_client_action as ca
        fake = FakeUnifi()
        out = ca.run(_ctx(fake), client_ids=["c-1", "c-2"], action="block")
        rows = _assert_batch(self, out)
        self.assertEqual([r["client_id"] for r in rows], ["c-1", "c-2"])
        self.assertTrue(all(w[2] == {"action": "BLOCK"} for w in fake.writes))
        self.assertEqual(fake.writes[0][1], "/v1/sites/site-1/clients/c-1/actions")
        # single path unchanged
        sfake = FakeUnifi()
        s = ca.run(_ctx(sfake), client_id="c-9", action="unblock")
        self.assertTrue(s["ok"])
        self.assertEqual(sfake.writes[0][2], {"action": "UNBLOCK"})

    def test_forget_device_batch_destructive(self):
        from execution.skills import unifi_forget_device as fd
        fake = FakeUnifi()
        out = fd.run(_ctx(fake), device_ids=["d-1", "d-2"])
        rows = _assert_batch(self, out)
        self.assertEqual([r["device_id"] for r in rows], ["d-1", "d-2"])
        self.assertEqual(fake.writes[0], ("DELETE", "/v1/sites/site-1/devices/d-1", None))
        self.assertEqual(fake.writes[1], ("DELETE", "/v1/sites/site-1/devices/d-2", None))


if __name__ == "__main__":
    unittest.main()
