"""Cylance + Huntress read/write skills (D-82) — validation + correct path/body via fakes."""
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


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda integ, tenant: fake)


class CylanceSkills(unittest.TestCase):
    def test_assign_policy_reads_name_then_puts(self):
        from execution.skills import cylance_assign_policy as ap
        fake = FakeEDR(get_data={"name": "PC-1", "id": "dev-1"})
        r = ap.run(_ctx(fake), device_id="dev-1", policy_id="pol-9")
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.gets[0][0], "/devices/v2/dev-1")           # fetched current name
        self.assertEqual(fake.writes[0], ("PUT", "/devices/v2/dev-1",
                                          {"name": "PC-1", "policy_id": "pol-9"}))

    def test_update_threat_action_and_validation(self):
        from execution.skills import cylance_update_threat as ut
        fake = FakeEDR()
        r = ut.run(_ctx(fake), device_id="dev-1", sha256="a"*64, action="waive")
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.writes[0], ("PUT", "/devices/v2/dev-1/threats",
                                          {"threat_id": "a"*64, "event": "Waive"}))
        self.assertFalse(ut.run(_ctx(FakeEDR()), device_id="d", sha256="nothex", action="waive")["ok"])
        self.assertFalse(ut.run(_ctx(FakeEDR()), device_id="d", sha256="a"*64, action="nuke")["ok"])

    def test_globallist_add_remove(self):
        from execution.skills import cylance_globallist_add as ga, cylance_globallist_remove as gr
        fa = FakeEDR()
        ga.run(_ctx(fa), list="safe", sha256="b"*64, reason="known good", category="Drivers")
        m, p, body = fa.writes[0]
        self.assertEqual((m, p), ("POST", "/globallists/v2"))
        self.assertEqual(body["list_type"], "GlobalSafe")
        self.assertEqual(body["category"], "Drivers")
        fr = FakeEDR()
        gr.run(_ctx(fr), list="quarantine", sha256="b"*64)
        self.assertEqual(fr.writes[0], ("DELETE", "/globallists/v2",
                                        {"sha256": "b"*64, "list_type": "GlobalQuarantine"}))

    def test_delete_device_is_destructive_path(self):
        from execution.skills import cylance_delete_device as dd
        fake = FakeEDR()
        dd.run(_ctx(fake), device_id="dev-1")
        self.assertEqual(fake.writes[0], ("DELETE", "/devices/v2", {"device_ids": ["dev-1"]}))

    def test_global_list_read_maps_list_type(self):
        from execution.skills import cylance_global_list as gl
        fake = FakeEDR(rows=[{"sha256": "c"*64, "name": "x"}])
        gl.run(_ctx(fake), list="quarantine")
        self.assertEqual(fake.gets[0], ("/globallists/v2", {"listTypeId": 0}))


class HuntressSkills(unittest.TestCase):
    def test_resolve_escalation_path(self):
        from execution.skills import huntress_resolve_escalation as re_
        fake = FakeEDR()
        r = re_.run(_ctx(fake), escalation_id="42", note="benign")
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.writes[0], ("POST", "/escalations/42/resolution", {"note": "benign"}))
        self.assertFalse(re_.run(_ctx(FakeEDR()), escalation_id="not-num")["ok"])

    def test_remediation_respond_uses_account_id(self):
        from execution.skills import huntress_remediation_respond as rr
        fake = FakeEDR(get_data={"id": 7, "name": "Acme MSP"})
        r = rr.run(_ctx(fake), incident_id="9", action="approve")
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.gets[0][0], "/account")
        self.assertEqual(fake.writes[0][1],
                         "/accounts/7/incident_reports/9/remediations/bulk_approval")
        self.assertFalse(rr.run(_ctx(FakeEDR({"id": 7})), incident_id="9", action="boom")["ok"])

    def test_list_organizations_filters(self):
        from execution.skills import huntress_list_organizations as lo
        fake = FakeEDR(rows=[{"id": 1, "name": "ACME"}, {"id": 2, "name": "Globex"}])
        out = lo.run(_ctx(fake), name_contains="acme")
        self.assertEqual([o["name"] for o in out], ["ACME"])


if __name__ == "__main__":
    unittest.main()
