"""Google Workspace Phase 4 (D-118) — shared drives + onboard/offboard composites, no network."""
import unittest

from execution.core.context import ToolContext
from execution.clients.scopes import is_allowed_read, is_allowed_write


class FakeG:
    """Routes GETs by path; records writes/deletes. `user_exists` toggles the create-user path."""
    def __init__(self, *, user_exists=True, groups=()):
        self.calls = []
        self.user_exists = user_exists
        self._groups = list(groups)

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        if path.startswith("/admin/directory/v1/groups") and (params or {}).get("userKey"):
            return {"groups": [{"email": g} for g in self._groups]}
        if "/users/" in path:
            if not self.user_exists:
                return {}
            email = path.split("/users/")[1].split("?")[0]
            return {"primaryEmail": email, "id": "id-" + email.split("@")[0]}
        return {}

    def post(self, path, body=None):
        self.calls.append(("POST", path, body)); return {"id": "new-1"}

    def patch(self, path, body=None):
        self.calls.append(("PATCH", path, body)); return {"id": "1"}

    def delete(self, path, body=None):
        self.calls.append(("DELETE", path, None)); return {"ok": True}


def _ctx(fake):
    return ToolContext(tenant_id="acme", actor="t", client_factory=lambda i, t: fake)


def _posts(f):
    return [c for c in f.calls if c[0] == "POST"]


class Allowlist(unittest.TestCase):
    def test_drive_and_datatransfer_bounds(self):
        self.assertTrue(is_allowed_read("gws", "/drive/v3/drives")[0])
        self.assertTrue(is_allowed_write("gws", "/drive/v3/drives", "POST")[0])
        self.assertTrue(is_allowed_write("gws", "/drive/v3/files/abc/permissions", "POST")[0])
        self.assertTrue(is_allowed_write("gws", "/admin/datatransfer/v1/transfers", "POST")[0])
        # still fail-closed elsewhere
        self.assertFalse(is_allowed_write("gws", "/gmail/v1/users/me/settings/forwarding", "POST")[0])


class SharedDrives(unittest.TestCase):
    def test_create_shared_drive_posts_with_request_id(self):
        from execution.skills import gws_create_shared_drive
        f = FakeG()
        out = gws_create_shared_drive.run(_ctx(f), name="Finance")
        self.assertTrue(out["ok"])
        p = _posts(f)[0]
        self.assertTrue(p[1].startswith("/drive/v3/drives?requestId="))
        self.assertEqual(p[2], {"name": "Finance"})
        self.assertEqual(out["drive_id"], "new-1")

    def test_add_member_permissions_path_and_body(self):
        from execution.skills import gws_add_shared_drive_member
        f = FakeG()
        out = gws_add_shared_drive_member.run(_ctx(f), drive_id="D1", member="jane@acme.com",
                                              role="organizer")
        self.assertTrue(out["ok"])
        p = _posts(f)[0]
        self.assertTrue(p[1].startswith("/drive/v3/files/D1/permissions"))
        self.assertIn("useDomainAdminAccess=true", p[1])
        self.assertEqual(p[2], {"type": "user", "role": "organizer", "emailAddress": "jane@acme.com"})


class Onboard(unittest.TestCase):
    def test_existing_user_license_and_group(self):
        from execution.skills import gws_onboard_user
        f = FakeG(user_exists=True)
        out = gws_onboard_user.run(_ctx(f), user="jane@acme.com", license_sku="1010020028",
                                   groups=[{"group": "sales@acme.com", "role": "MEMBER"}])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["steps"]["license"], "done")
        self.assertEqual(out["steps"]["groups"]["sales@acme.com"], "done")
        # license POST + group-member POST both happened
        paths = [p[1] for p in _posts(f)]
        self.assertTrue(any("/apps/licensing/" in x for x in paths))
        self.assertTrue(any("/groups/sales@acme.com/members" in x for x in paths))

    def test_missing_user_without_names_errors(self):
        from execution.skills import gws_onboard_user
        out = gws_onboard_user.run(_ctx(FakeG(user_exists=False)), user="new@acme.com")
        self.assertFalse(out["ok"])
        self.assertIn("doesn't exist", out["error"])


class Offboard(unittest.TestCase):
    def test_suspend_remove_groups_and_transfer(self):
        from execution.skills import gws_offboard_user
        f = FakeG(user_exists=True, groups=["sales@acme.com", "all@acme.com"])
        out = gws_offboard_user.run(_ctx(f), user="bob@acme.com", transfer_drive_to="mgr@acme.com",
                                    remove_license_sku="1010020028")
        self.assertTrue(out["ok"], out)
        # suspended
        self.assertTrue(any(c[0] == "PATCH" and c[2] == {"suspended": True} for c in f.calls))
        # removed from both groups
        deletes = [c[1] for c in f.calls if c[0] == "DELETE" and "/members/" in c[1]]
        self.assertEqual(len(deletes), 2)
        # drive transfer POST with the well-known Drive app id + resolved numeric ids
        tr = [c for c in f.calls if c[0] == "POST" and c[1] == "/admin/datatransfer/v1/transfers"]
        self.assertEqual(len(tr), 1)
        self.assertEqual(tr[0][2]["applicationDataTransfers"][0]["applicationId"], "55656082996")
        self.assertEqual(tr[0][2]["oldOwnerUserId"], "id-bob")
        self.assertEqual(tr[0][2]["newOwnerUserId"], "id-mgr")


if __name__ == "__main__":
    unittest.main()
