"""ConversationStore tests — per-user persistence, ownership scoping, auto-title, compact."""
import tempfile
import unittest
from pathlib import Path

from execution.core.conversations import ConversationStore


class Conversations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ConversationStore(Path(self.tmp.name) / "c.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_create_list_get(self):
        c = self.store.create("alice", tenant_id="acme")
        self.assertEqual(c["tenant_id"], "acme")
        lst = self.store.list("alice")
        self.assertEqual(len(lst), 1)
        self.assertEqual(lst[0]["message_count"], 0)
        full = self.store.get("alice", c["id"])
        self.assertEqual(full["messages"], [])

    def test_auto_title_from_first_user_message(self):
        c = self.store.create("alice")
        self.store.add_message("alice", c["id"], "user", "which acme users have MFA disabled?")
        self.assertEqual(self.store.list("alice")[0]["title"], "which acme users have MFA disabled?")
        # a later message does NOT overwrite the title
        self.store.add_message("alice", c["id"], "assistant", "none")
        self.assertEqual(self.store.list("alice")[0]["title"], "which acme users have MFA disabled?")

    def test_ownership_isolation(self):
        c = self.store.create("alice", tenant_id="acme")
        self.store.add_message("alice", c["id"], "user", "secret question")
        # bob cannot see, read, rename, append to, or delete alice's conversation
        self.assertEqual(self.store.list("bob"), [])
        self.assertIsNone(self.store.get("bob", c["id"]))
        self.assertFalse(self.store.owns("bob", c["id"]))
        self.assertFalse(self.store.add_message("bob", c["id"], "user", "intrude"))
        self.assertFalse(self.store.rename("bob", c["id"], "hijack"))
        self.assertFalse(self.store.delete("bob", c["id"]))
        # alice's data is intact
        self.assertEqual(len(self.store.get("alice", c["id"])["messages"]), 1)

    def test_history_order_and_meta_roundtrip(self):
        c = self.store.create("alice")
        self.store.add_message("alice", c["id"], "user", "q1")
        self.store.add_message("alice", c["id"], "assistant", "a1",
                               meta={"tools": [{"name": "x", "ok": True}], "citations": ["src@acme"]})
        hist = self.store.history("alice", c["id"])
        self.assertEqual([h["role"] for h in hist], ["user", "assistant"])  # oldest→newest
        msgs = self.store.get("alice", c["id"])["messages"]
        self.assertEqual(msgs[1]["meta"]["citations"], ["src@acme"])        # JSON meta survives

    def test_delete_removes_messages(self):
        c = self.store.create("alice")
        self.store.add_message("alice", c["id"], "user", "q")
        self.assertTrue(self.store.delete("alice", c["id"]))
        self.assertEqual(self.store.list("alice"), [])
        self.assertIsNone(self.store.get("alice", c["id"]))

    def test_compact_keeps_tail_and_prepends_summary(self):
        c = self.store.create("alice")
        for i in range(6):
            self.store.add_message("alice", c["id"], "user" if i % 2 == 0 else "assistant", f"m{i}")
        self.assertTrue(self.store.compact("alice", c["id"], "SUMMARY HERE", keep=2))
        msgs = self.store.get("alice", c["id"])["messages"]
        self.assertEqual(len(msgs), 3)                       # 1 summary + 2 kept
        self.assertIn("SUMMARY HERE", msgs[0]["content"])    # summary first
        self.assertTrue(msgs[0]["meta"]["compacted"])
        self.assertEqual([m["content"] for m in msgs[1:]], ["m4", "m5"])  # tail preserved in order


if __name__ == "__main__":
    unittest.main()
