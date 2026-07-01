"""Google Workspace WRITE skills (D-118) — scope allowlist + skill behavior with a fake client."""
import unittest

from execution.core.context import ToolContext
from execution.clients.scopes import is_allowed_write, is_allowed_delete


class FakeGoogle:
    """Records writes; answers user GETs (for create-user's verify re-read)."""
    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        if "/users/" in path:
            return {"primaryEmail": path.split("/users/")[1].split("?")[0]}
        return {}

    def post(self, path, body=None):
        self.calls.append(("POST", path, body)); return {"id": "1"}

    def patch(self, path, body=None):
        self.calls.append(("PATCH", path, body)); return {"id": "1"}

    def delete(self, path, body=None):
        self.calls.append(("DELETE", path, None)); return {"ok": True}


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


class Allowlist(unittest.TestCase):
    def test_writes_bounded_to_intended_surface(self):
        ok_w = [("/admin/directory/v1/users", "POST"),
                ("/admin/directory/v1/users/a@b.com", "PATCH"),
                ("/admin/directory/v1/groups", "POST"),
                ("/admin/directory/v1/groups/g@b.com/members", "POST"),
                ("/apps/licensing/v1/product/Google-Apps/sku/x/user", "POST")]
        for path, m in ok_w:
            self.assertTrue(is_allowed_write("gws", path, m)[0], (path, m))
        # not writable → fail closed
        self.assertFalse(is_allowed_write("gws", "/admin/directory/v1/domains", "POST")[0])
        self.assertFalse(is_allowed_write("gws", "/drive/v3/drives", "POST")[0])
        self.assertFalse(is_allowed_write("gws", "/admin/directory/v1/users", "DELETE")[0])

    def test_deletes_bounded_and_user_delete_forbidden(self):
        self.assertTrue(is_allowed_delete("gws", "/admin/directory/v1/groups/g/members/m")[0])
        self.assertTrue(is_allowed_delete("gws", "/admin/directory/v1/groups/g")[0])
        self.assertTrue(is_allowed_delete("gws", "/apps/licensing/v1/product/p/sku/s/user/u")[0])
        # deleting a USER is intentionally NOT permitted (offboarding suspends)
        self.assertFalse(is_allowed_delete("gws", "/admin/directory/v1/users/x")[0])


class Skills(unittest.TestCase):
    def test_create_user_body_and_password(self):
        from execution.skills import gws_create_user
        f = FakeGoogle()
        out = gws_create_user.run(_ctx(f), email="jane@acme.com", first_name="Jane",
                                  last_name="Doe", org_unit_path="/Sales")
        self.assertTrue(out["ok"])
        post = [c for c in f.calls if c[0] == "POST"][0]
        self.assertEqual(post[1], "/admin/directory/v1/users")
        self.assertEqual(post[2]["primaryEmail"], "jane@acme.com")
        self.assertEqual(post[2]["name"], {"givenName": "Jane", "familyName": "Doe"})
        self.assertEqual(post[2]["orgUnitPath"], "/Sales")
        self.assertTrue(post[2]["changePasswordAtNextLogin"])
        self.assertGreaterEqual(len(post[2]["password"]), 16)     # server-generated
        self.assertTrue(out["verified"])                          # re-read succeeded
        self.assertIn("initial_password", out)

    def test_create_user_requires_names(self):
        from execution.skills import gws_create_user
        out = gws_create_user.run(_ctx(FakeGoogle()), email="x@acme.com", first_name="", last_name="")
        self.assertFalse(out["ok"])

    def test_suspend_patches_suspended_true(self):
        from execution.skills import gws_suspend_user
        f = FakeGoogle()
        out = gws_suspend_user.run(_ctx(f), user="bob@acme.com")
        self.assertTrue(out["ok"])
        self.assertEqual(f.calls[0], ("PATCH", "/admin/directory/v1/users/bob@acme.com",
                                      {"suspended": True}))

    def test_add_group_member_role(self):
        from execution.skills import gws_add_group_member
        f = FakeGoogle()
        out = gws_add_group_member.run(_ctx(f), group="sales@acme.com", member="jane@acme.com",
                                       role="manager")
        self.assertTrue(out["ok"])
        self.assertEqual(f.calls[0], ("POST", "/admin/directory/v1/groups/sales@acme.com/members",
                                      {"email": "jane@acme.com", "role": "MANAGER"}))

    def test_remove_group_member_deletes(self):
        from execution.skills import gws_remove_group_member
        f = FakeGoogle()
        out = gws_remove_group_member.run(_ctx(f), group="sales@acme.com", member="jane@acme.com")
        self.assertTrue(out["ok"])
        self.assertEqual(f.calls[0][0], "DELETE")
        self.assertTrue(f.calls[0][1].endswith("/groups/sales@acme.com/members/jane@acme.com"))

    def test_assign_license_path_and_body(self):
        from execution.skills import gws_assign_license
        f = FakeGoogle()
        out = gws_assign_license.run(_ctx(f), user="jane@acme.com", sku="1010020028")
        self.assertTrue(out["ok"])
        self.assertEqual(out["sku_name"], "Business Standard")
        self.assertEqual(f.calls[0], ("POST",
                         "/apps/licensing/v1/product/Google-Apps/sku/1010020028/user",
                         {"userId": "jane@acme.com"}))


if __name__ == "__main__":
    unittest.main()
