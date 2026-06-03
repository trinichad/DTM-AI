"""Web API tests — auth gating, capability edits, chat, all without sockets."""
import tempfile
import unittest
from pathlib import Path

from execution.runtime import build_agent
from execution.web.api import Api
from execution.web.auth import AuthStore, SessionSigner


class WebApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "w.db"
        self.agent = build_agent(db_path=db)
        self.auth = AuthStore(db)
        self.auth.ensure_admin("secret")
        self.signer = SessionSigner(secret=b"0" * 32)
        self.api = Api(self.agent, self.auth, self.signer)

    def tearDown(self):
        self.auth.close()
        self.tmp.cleanup()

    def H(self, method, path, body=None, query=None, user=None):
        return self.api.handle(method, path, query or {}, body or {}, user)

    def test_auth_required(self):
        self.assertEqual(self.H("GET", "/api/tools").status, 401)

    def test_login_flow(self):
        self.assertEqual(self.H("POST", "/api/login", {"username": "admin", "password": "no"}).status, 401)
        r = self.H("POST", "/api/login", {"username": "admin", "password": "secret"})
        self.assertEqual(r.status, 200)
        self.assertIsNotNone(r.set_cookie)
        self.assertEqual(self.signer.verify(r.set_cookie), "admin")

    def test_tools_listed_with_policy(self):
        r = self.H("GET", "/api/tools", user="admin")
        names = {t["name"] for t in r.payload["tools"]}
        self.assertIn("system_health", names)
        self.assertIn("kaseya_list_assets", names)
        for t in r.payload["tools"]:
            self.assertIn("allow_write", t)

    def test_capability_edit_persists(self):
        self.H("POST", "/api/capabilities/echo_note", {"allow_write": True, "require_approval": False}, user="admin")
        tools = {t["name"]: t for t in self.H("GET", "/api/tools", user="admin").payload["tools"]}
        self.assertTrue(tools["echo_note"]["allow_write"])
        self.assertFalse(tools["echo_note"]["require_approval"])

    def test_capability_unknown_tool_404(self):
        self.assertEqual(self.H("POST", "/api/capabilities/nope", {"enabled": True}, user="admin").status, 404)

    def test_chat(self):
        r = self.H("POST", "/api/chat", {"tenant": "acme", "message": "status?"}, user="admin")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.payload["tenant"], "acme")
        self.assertIn("answer", r.payload)

    def test_chat_requires_message(self):
        self.assertEqual(self.H("POST", "/api/chat", {"tenant": "acme"}, user="admin").status, 400)

    def test_fleet_counts_structure(self):
        self.assertEqual(self.H("GET", "/api/fleet").status, 401)         # auth gated
        r = self.H("GET", "/api/fleet", user="admin")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.payload["tenant"], "*")
        names = {f["name"] for f in r.payload["fleet"]}
        self.assertIn("kaseya_list_assets", names)                        # fleet tool present
        for f in r.payload["fleet"]:
            self.assertIn("count", f)
            self.assertIn("ok", f)        # no creds in test -> ok False, count None (fail-closed, no network)

    def test_models_exposes_context_cap(self):
        r = self.H("GET", "/api/models", user="admin")
        self.assertIn("context", r.payload)
        self.assertGreater(r.payload["context"]["history_chars"], 0)

    def test_session_expiry(self):
        token = self.signer.make("admin", ttl_minutes=-1)  # already expired
        self.assertIsNone(self.signer.verify(token))


if __name__ == "__main__":
    unittest.main()
