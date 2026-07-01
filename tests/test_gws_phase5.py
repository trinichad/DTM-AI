"""Google Workspace Phase 5 (D-118) — devices + audit reports, no network."""
import unittest

from execution.core.context import ToolContext
from execution.clients.scopes import is_allowed_read, is_allowed_write


class FakeG:
    def __init__(self, get_reply=None):
        self.calls = []
        self._get = get_reply or {}

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return self._get

    def post(self, path, body=None):
        self.calls.append(("POST", path, body)); return {"ok": True}


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


class Allowlist(unittest.TestCase):
    def test_reports_read_and_device_action_write_bounds(self):
        self.assertTrue(is_allowed_read("gws", "/admin/reports/v1/activity/users/all/applications/login")[0])
        self.assertTrue(is_allowed_read("gws", "/admin/directory/v1/customer/my_customer/devices/mobile")[0])
        self.assertTrue(is_allowed_write(
            "gws", "/admin/directory/v1/customer/my_customer/devices/mobile/RID/action", "POST")[0])
        # a device action must NOT open up arbitrary customer-scoped writes (e.g. creating org units)
        self.assertFalse(is_allowed_write(
            "gws", "/admin/directory/v1/customer/my_customer/orgunits", "POST")[0])
        # reports are read-only
        self.assertFalse(is_allowed_write("gws", "/admin/reports/v1/activity", "POST")[0])


class Devices(unittest.TestCase):
    def test_list_mobile_slims_and_keeps_resource_id(self):
        from execution.skills import gws_list_mobile_devices
        f = FakeG({"mobiledevices": [
            {"resourceId": "RID1", "email": ["jane@acme.com"], "model": "Pixel 8",
             "os": "Android 15", "type": "ANDROID", "status": "APPROVED"}]})
        out = gws_list_mobile_devices.run(_ctx(f))
        self.assertEqual(out["count"], 1)
        d = out["mobile_devices"][0]
        self.assertEqual(d["owner"], "jane@acme.com")
        self.assertEqual(d["resourceId"], "RID1")           # needed downstream by the wipe tool
        self.assertEqual(f.calls[0][1], "/admin/directory/v1/customer/my_customer/devices/mobile")

    def test_wipe_default_action_is_account_wipe(self):
        from execution.skills import gws_wipe_mobile_device
        f = FakeG()
        out = gws_wipe_mobile_device.run(_ctx(f), resource_id="RID1")
        self.assertTrue(out["ok"])
        self.assertEqual(f.calls[0], ("POST",
            "/admin/directory/v1/customer/my_customer/devices/mobile/RID1/action",
            {"action": "admin_account_wipe"}))

    def test_wipe_full_maps_to_remote_wipe(self):
        from execution.skills import gws_wipe_mobile_device
        f = FakeG()
        gws_wipe_mobile_device.run(_ctx(f), resource_id="RID1", action="full_wipe")
        self.assertEqual(f.calls[0][2], {"action": "admin_remote_wipe"})

    def test_wipe_requires_resource_id(self):
        from execution.skills import gws_wipe_mobile_device
        out = gws_wipe_mobile_device.run(_ctx(FakeG()), resource_id="")
        self.assertFalse(out["ok"])


class Reports(unittest.TestCase):
    def test_audit_log_path_and_slim(self):
        from execution.skills import gws_audit_log
        f = FakeG({"items": [
            {"id": {"time": "2026-07-01T00:00:00Z"}, "actor": {"email": "admin@acme.com"},
             "ipAddress": "1.2.3.4",
             "events": [{"name": "login_success", "type": "login",
                         "parameters": [{"name": "login_type", "value": "google_password"}]}]}]})
        out = gws_audit_log.run(_ctx(f), application="login", user="admin@acme.com")
        self.assertEqual(f.calls[0][1],
                         "/admin/reports/v1/activity/users/admin@acme.com/applications/login")
        self.assertEqual(out["count"], 1)
        ev = out["events"][0]
        self.assertEqual(ev["actor"], "admin@acme.com")
        self.assertEqual(ev["events"][0]["name"], "login_success")
        self.assertEqual(ev["events"][0]["details"], {"login_type": "google_password"})

    def test_audit_log_defaults_to_all_users_login(self):
        from execution.skills import gws_audit_log
        f = FakeG({"items": []})
        gws_audit_log.run(_ctx(f))
        self.assertEqual(f.calls[0][1], "/admin/reports/v1/activity/users/all/applications/login")


if __name__ == "__main__":
    unittest.main()
