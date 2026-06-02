"""User account tests — AuthStore CRUD + API gating."""
import tempfile
import unittest
from pathlib import Path

from execution.runtime import build_agent
from execution.web.api import Api
from execution.web.auth import AuthStore, SessionSigner


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = AuthStore(Path(self.tmp.name) / "u.db")
        self.a.ensure_admin("adminpass")

    def tearDown(self):
        self.a.close()
        self.tmp.cleanup()

    def test_create_and_login(self):
        self.a.create_user("tech1", "techpass1", "user", "t1@dtm.com")
        self.assertEqual(self.a.verify_login("tech1", "techpass1"), "user")
        self.assertEqual(self.a.get_user("tech1")["email"], "t1@dtm.com")

    def test_no_duplicate(self):
        self.a.create_user("t", "pw12345678")
        with self.assertRaises(ValueError):
            self.a.create_user("t", "other12345")

    def test_cannot_delete_last_admin(self):
        with self.assertRaises(ValueError):
            self.a.delete_user("admin")

    def test_cannot_demote_last_admin(self):
        with self.assertRaises(ValueError):
            self.a.update_user("admin", role="user")

    def test_delete_and_role_update(self):
        self.a.create_user("t", "pw12345678", "user")
        self.a.update_user("t", role="admin")
        self.assertEqual(self.a.get_role("t"), "admin")
        self.a.delete_user("t")
        self.assertIsNone(self.a.get_role("t"))


class ApiGating(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "w.db"
        self.agent = build_agent(db_path=db)
        self.auth = AuthStore(db)
        self.auth.ensure_admin("adminpass")
        self.auth.create_user("tech1", "techpass1", "user")
        self.api = Api(self.agent, self.auth, SessionSigner(secret=b"0" * 32))

    def tearDown(self):
        self.auth.close()
        self.tmp.cleanup()

    def H(self, m, p, b=None, u=None):
        return self.api.handle(m, p, {}, b or {}, u)

    def test_me_returns_role(self):
        self.assertEqual(self.H("GET", "/api/me", u="tech1").payload["role"], "user")

    def test_non_admin_cannot_list_users(self):
        self.assertEqual(self.H("GET", "/api/users", u="tech1").status, 403)

    def test_admin_can_create_and_delete(self):
        r = self.H("POST", "/api/users", {"username": "t2", "password": "pw12345678", "role": "user"}, u="admin")
        self.assertEqual(r.status, 200)
        self.assertTrue(any(x["username"] == "t2" for x in r.payload["users"]))
        self.assertEqual(self.H("DELETE", "/api/users/t2", u="admin").status, 200)

    def test_cannot_delete_self(self):
        self.assertEqual(self.H("DELETE", "/api/users/admin", u="admin").status, 400)

    def test_change_own_password(self):
        self.assertEqual(self.H("POST", "/api/me/password", {"current": "wrong", "new": "newpass12"}, u="tech1").status, 400)
        self.assertEqual(self.H("POST", "/api/me/password", {"current": "techpass1", "new": "newpass12"}, u="tech1").status, 200)
        self.assertEqual(self.auth.verify_login("tech1", "newpass12"), "user")

    def test_deleted_user_session_rejected(self):
        self.auth.delete_user("tech1")
        self.assertEqual(self.H("GET", "/api/tools", u="tech1").status, 401)


if __name__ == "__main__":
    unittest.main()
