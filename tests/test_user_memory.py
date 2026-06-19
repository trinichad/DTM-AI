"""Per-user profile memory tests (D-31) — storage, prompt injection, tool binding, Teams link."""
import os
import tempfile
import unittest
from pathlib import Path

from execution.core.memory import VaultStore


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.v = VaultStore(path=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_read(self):
        r = self.v.append_user_memory("alex", "Preferred email: user@demodomain.com", "alex")
        self.assertTrue(r["ok"])
        text = self.v.read_user_memory("alex")
        self.assertIn("Profile — alex", text)
        self.assertIn("user@demodomain.com", text)

    def test_overwrite_keeps_backup(self):
        self.v.append_user_memory("alex", "old fact", "alex")
        self.v.write_user_memory("alex", "# Profile — alex\n- new fact only", "agent")
        self.assertNotIn("old fact", self.v.read_user_memory("alex"))
        bak = Path(self.tmp.name) / "users" / "alex.md.bak"
        self.assertIn("old fact", bak.read_text())

    def test_empty_username_fails(self):
        self.assertIn("error", self.v.append_user_memory("", "x", "a"))
        self.assertIn("error", self.v.write_user_memory("", "x", "a"))

    def test_username_sanitized_no_traversal(self):
        self.v.append_user_memory("../../etc/passwd", "x", "a")
        self.assertFalse((Path(self.tmp.name).parent / "etc").exists())
        self.assertTrue(list((Path(self.tmp.name) / "users").glob("*.md")))


class PromptInjection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MSPAI_VAULT_PATH"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("MSPAI_VAULT_PATH", None)
        self.tmp.cleanup()

    def test_user_block_with_email_and_memory(self):
        from execution.agent import build_system_prompt
        VaultStore(path=Path(self.tmp.name)).append_user_memory(
            "alex", "Prefers concise answers", "alex")
        p = build_system_prompt(user_profile={"username": "alex",
                                              "email": "user@demodomain.com", "role": "admin"})
        self.assertIn("The person you are talking to", p)
        self.assertIn("user@demodomain.com", p)
        self.assertIn("Prefers concise answers", p)
        self.assertIn("ask whether to UPDATE it, KEEP it, or ADD", p)   # conflict protocol

    def test_no_user_profile_no_block(self):
        from execution.agent import build_system_prompt
        self.assertNotIn("The person you are talking to", build_system_prompt())


class ToolBinding(unittest.TestCase):
    """The tools write the ctx-bound user only — never a model-chosen target."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MSPAI_VAULT_PATH"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("MSPAI_VAULT_PATH", None)
        self.tmp.cleanup()

    def _ctx(self, profile):
        from execution.core.context import ToolContext
        return ToolContext(tenant_id="*", actor="test", _meta={"user_profile": profile})

    def test_note_writes_bound_user(self):
        from execution.skills import user_memory_note
        r = user_memory_note.run(self._ctx({"username": "alex"}), note="cell 555-0100")
        self.assertTrue(r["ok"])
        self.assertIn("555-0100", VaultStore(path=Path(self.tmp.name)).read_user_memory("alex"))

    def test_no_bound_user_errors(self):
        from execution.skills import user_memory_note, user_memory_update
        self.assertIn("error", user_memory_note.run(self._ctx({}), note="x"))
        self.assertIn("error", user_memory_update.run(self._ctx(None), content="x"))

    def test_send_email_me_resolves_user(self):
        from execution.skills import send_email
        sent = {}

        class FakeEmail:
            def send(self, subject, body, to=None, html=False, html_body=""):
                sent.update({"to": to}); return {"ok": True, "to": to}

        from execution.core.context import ToolContext
        ctx = ToolContext(tenant_id="*", actor="t",
                          client_factory=lambda i, t: FakeEmail(),
                          _meta={"user_profile": {"username": "alex",
                                                  "email": "user@demodomain.com"}})
        r = send_email.run(ctx, subject="s", body="b", to="me")
        self.assertEqual(sent["to"], "user@demodomain.com")
        # "me" also resolves inside a multi-recipient list (D-46)
        send_email.run(ctx, subject="s", body="b", to="me, other@demodomain.com")
        self.assertEqual(sent["to"], "user@demodomain.com, other@demodomain.com")
        # no email on file → refuse with a clear error, never guess
        ctx2 = ToolContext(tenant_id="*", actor="t", client_factory=lambda i, t: FakeEmail(),
                           _meta={"user_profile": {"username": "x", "email": ""}})
        self.assertFalse(send_email.run(ctx2, subject="s", body="b", to="me")["ok"])


class TeamsIdentityLink(unittest.TestCase):
    def test_allowlist_three_part_entries(self):
        from execution.clients.msteams import parse_allowlist
        e = parse_allowlist("aad-1|Alex|alex, aad-2|Dana, aad-3")
        self.assertEqual(e[0], {"id": "aad-1", "name": "Alex", "user": "alex"})
        self.assertEqual(e[1]["user"], "")
        self.assertEqual(e[2], {"id": "aad-3", "name": "", "user": ""})

    def test_bridge_resolves_linked_account(self):
        from unittest import mock
        from execution.core.teams_bot import TeamsBridge
        accounts = {"alex": {"username": "alex", "email": "user@demodomain.com", "role": "admin"}}
        b = TeamsBridge(mock.Mock(), user_lookup=accounts.get)
        env = {"TEAMS_ALLOWED_USERS": "aad-1|Alex|alex"}
        p = b._user_profile(env, "aad-1", "Alex R")
        self.assertEqual(p["username"], "alex")
        self.assertEqual(p["email"], "user@demodomain.com")
        # unlinked teams user → teams-scoped identity, no email
        p2 = b._user_profile(env, "aad-9", "Visitor")
        self.assertEqual(p2["username"], "teams:aad-9")
        self.assertEqual(p2["email"], "")


if __name__ == "__main__":
    unittest.main()
