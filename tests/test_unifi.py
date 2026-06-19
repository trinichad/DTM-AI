"""UniFi Network connector (D-84) — client write surface + skill path/body via fakes."""
import unittest

from execution.core.context import ToolContext
from execution.clients.unifi import UnifiClient


class UnifiClientTests(unittest.TestCase):
    def test_base_url_and_tls_passthrough(self):
        seen = {}

        def t(method, url, headers=None, params=None, json_body=None, verify_tls=True):
            seen["url"], seen["verify"] = url, verify_tls
            return 200, {"data": []}
        c = UnifiClient("https://unifi.x:8443", "k", verify_tls=False, transport=t)
        c.get("/v1/sites")
        self.assertEqual(seen["url"], "https://unifi.x:8443/proxy/network/integration/v1/sites")
        self.assertFalse(seen["verify"])                  # self-signed honored

    def test_paginate_unwraps_data(self):
        def t(method, url, headers=None, params=None, json_body=None, verify_tls=True):
            off = (params or {}).get("offset", 0)
            if off == 0:
                return 200, {"data": [{"id": "a"}, {"id": "b"}], "totalCount": 3}
            return 200, {"data": [{"id": "c"}], "totalCount": 3}
        c = UnifiClient("https://x", "k", transport=t)
        self.assertEqual([r["id"] for r in c.get_paginated("/v1/sites/s/clients", limit=2)],
                         ["a", "b", "c"])

    def test_write_allowlist_and_destructive_split(self):
        calls = []

        def t(method, url, headers=None, params=None, json_body=None, verify_tls=True):
            calls.append((method, url.rsplit("/integration", 1)[-1]))
            return 200, {"ok": True}
        c = UnifiClient("https://x", "k", transport=t)
        self.assertNotIn("error", c.write("POST", "/v1/sites/s/devices/d/actions", {"action": "RESTART"}))
        self.assertNotIn("error", c.write("PUT", "/v1/sites/s/firewall/policies/p", {}))
        self.assertIn("error", c.write("DELETE", "/v1/sites/s/devices/d"))     # destructive path
        self.assertIn("error", c.write("POST", "/v1/sites/s/anything", {}))    # not allow-listed
        self.assertNotIn("error", c.write_destructive("DELETE", "/v1/sites/s/devices/d"))
        self.assertIn("error", c.write_destructive("PUT", "/v1/sites/s/devices/d", {}))
        self.assertEqual(len(calls), 3)


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


class UnifiSkills(unittest.TestCase):
    def test_restart_resolves_default_site(self):
        from execution.skills import unifi_restart_device as rd
        fake = FakeUnifi()
        r = rd.run(_ctx(fake), device_id="dev-9")
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.writes[0], ("POST", "/v1/sites/site-1/devices/dev-9/actions",
                                          {"action": "RESTART"}))

    def test_port_cycle_validates_and_builds(self):
        from execution.skills import unifi_port_cycle as pc
        fake = FakeUnifi()
        pc.run(_ctx(fake), device_id="sw-1", port=7)
        self.assertEqual(fake.writes[0], ("POST", "/v1/sites/site-1/devices/sw-1/interfaces/ports/7/actions",
                                          {"action": "POWER_CYCLE"}))
        self.assertFalse(pc.run(_ctx(FakeUnifi()), device_id="sw-1", port=99)["ok"])

    def test_client_action_maps_words(self):
        from execution.skills import unifi_client_action as ca
        fake = FakeUnifi()
        ca.run(_ctx(fake), client_id="c-1", action="block")
        self.assertEqual(fake.writes[0][2], {"action": "BLOCK"})
        self.assertFalse(ca.run(_ctx(FakeUnifi()), client_id="c-1", action="zap")["ok"])

    def test_forget_device_destructive_path(self):
        from execution.skills import unifi_forget_device as fd
        fake = FakeUnifi()
        fd.run(_ctx(fake), device_id="dev-9")
        self.assertEqual(fake.writes[0], ("DELETE", "/v1/sites/site-1/devices/dev-9", None))

    def test_generic_write_path_guard(self):
        from execution.skills import unifi_write as w
        self.assertFalse(w.run(_ctx(FakeUnifi()), method="GET", path="/v1/sites/s/x", body={})["ok"])
        self.assertFalse(w.run(_ctx(FakeUnifi()), method="POST", path="/etc/passwd", body={})["ok"])
        fake = FakeUnifi()
        w.run(_ctx(fake), method="POST", path="/v1/sites/s/networks", body={"name": "VLAN20"})
        self.assertEqual(fake.writes[0][:2], ("POST", "/v1/sites/s/networks"))

    def test_site_resolution_named(self):
        from execution.skills import unifi_list_clients as lc
        fake = FakeUnifi(sites=[{"id": "s1", "name": "HQ"}, {"id": "s2", "name": "Branch"}],
                         rows=[{"id": "c", "name": "PC1"}])
        lc.run(_ctx(fake), site="Branch")
        self.assertEqual(fake.gets[-1][0], "/v1/sites/s2/clients")

    def test_group_registered(self):
        from execution.core.tool_groups import GROUP_INFO
        self.assertIn("unifi", GROUP_INFO)
        self.assertIn("console", GROUP_INFO["unifi"]["setup"].lower())


if __name__ == "__main__":
    unittest.main()
